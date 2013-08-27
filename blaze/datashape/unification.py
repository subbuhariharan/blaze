# -*- coding: utf-8 -*-

"""
This module implements unification of datashapes. Unification is a general
problem that solves a system of equations between terms. In our case, the
terms are types (datashapes).

A difference with conventional unification is that our equations are not
necessarily looking for equality, they must account for coercions and
broadcasting.

A normalization phase accounts for broadcasting missing leading dimensions
by padding the types with one-sized dimensions. Coercion is resolved through
coercion constraints which act like subset relations. A unifier is
accompanied by a set of constraints but must hold for the free variables in
that type.
"""

from itertools import chain

from blaze import error
from blaze.py2help import dict_iteritems
from blaze.util import IdentityDict, IdentitySet
from . import promote_units, normalize, transform
from blaze.datashape.coretypes import Mono, TypeVar, free, type_constructor

#------------------------------------------------------------------------
# Entry points
#------------------------------------------------------------------------

def unify_simple(a, b):
    """Unify two blaze types"""
    return unify([(a, b)], [True])

def unify(constraints, broadcasting):
    """
    Unify a set of constraints and return a concrete solution

        >>> import blaze
        >>> d1 = blaze.dshape('10, int32')
        >>> d2 = blaze.dshape('T, float32')
        >>> [result], constraints = unify([(d1, d2)], [True])
        >>> result
        dshape("10, float32")
        >>> constraints
        []
    """
    # Compute a solution to a set of constraints
    constraints, b_env = normalize(constraints, broadcasting)
    solution, remaining = unify_constraints(constraints)
    resolve_typesets(remaining, solution)

    # Compute a type substitution with concrete types from the solution
    # TODO: incorporate broadcasting environment during reification
    substitution = reify(solution)

    # Reify and promote the datashapes
    result = [substitute(substitution, ds2) for ds1, ds2 in constraints]
    return result, [(a, solution[b]) for a, b in remaining]

#------------------------------------------------------------------------
# Unification
#------------------------------------------------------------------------

def unify_constraints(constraints):
    """
    Blaze type unification. Two types unify if:

        - They have the same type constructor, and
        - They have an equal number of sub-terms which unify element-wise

    Our algorithm is different from a conventional implementation in that we
    have a different notion of equality since we allow coercion which we
    solve by generating new coercion constraints in the form of
    [(A coerces to B)], where A and B are type variables.

    Parameters
    ----------
    constraints : [(DataShape, DataShape)]
        List of constraints (datashape type equations)

    Returns: { TypeVar : set([ Mono ]) }
        Returns a solution to the set of constraints. The solution is a set
        of bindings (a substitution) from type variables to type sets.
    """
    solution = IdentityDict()
    remaining = []

    # Initialize solution
    for t1, t2 in constraints:
        for freevar in chain(free(t1), free(t2)):
            solution[freevar] = set()

    # Calculate solution
    for t1, t2 in constraints:
        unify_single(t1, t2, solution, remaining)

    return solution, remaining

def unify_single(t1, t2, solution, remaining):
    """
    Unify a single type equation and update the solution and remaining
    constraints.
    """
    if isinstance(t1, TypeVar) and isinstance(t2, TypeVar):
        remaining.append((t1, t2))
    elif isinstance(t1, TypeVar):
        if t1 in free(t2):
            raise error.UnificationError("Cannot unify recursive types")
        solution[t1].add(t2)
    elif isinstance(t2, TypeVar):
        unify_single(t2, t1, solution, remaining)
    elif not free(t1) and not free(t2):
        # No need to recurse, this will be caught by promote()
        pass
    else:
        if not isinstance(t1, Mono) or not isinstance(t2, Mono):
            if t1 != t2:
                raise error.UnificationError("%s != %s" % (t1, t2))
            return

        args1, args2 = t1.parameters, t2.parameters
        tcon1, tcon2 = type_constructor(t1), type_constructor(t2)

        if tcon1 != tcon2:
            raise error.UnificationError(
                "Got differing type constructors %s and %s" % (tcon1, tcon2))

        if len(args1) != len(args2):
            raise error.UnificationError("%s got %d and %d arguments" % (
                tcon1, len(args1), len(args2)))

        for arg1, arg2 in zip(args1, args2):
            unify_single(arg1, arg2, solution, remaining)


def resolve_typesets(constraints, solution):
    """
    Fix-point iterate until all type variables are resolved according to the
    constraints.

    Parameters
    ----------
    constraints: [(TypeVar, TypeVar)]
        Constraint graph specifying coercion relations:
            (a, b) ∈ constraints with a ⊆ b

    solution : { TypeVar : set([ Type ]) }
        Typing solution
    """
    # Fix-point update our type sets
    changed = True
    while changed:
        changed = False
        for a, b in constraints:
            length = len(solution[b])
            solution[b].update(solution[a])
            changed |= length < len(solution[b])

    # Update empty type set solutions with variables from the input
    # We can do this since there is no contraint on the free variable
    empty = IdentitySet()
    for a, b in constraints:
        if not solution[b] or b in empty:
            solution[b].add(a)
            empty.add(b)

    # # Fix-point our fix-point
    # if empty:
    #     resolve_typesets(constraints, solution)

def reify(solution, S=None):
    """
    Reify a typing solution, returning a new solution with types as concrete
    types as opposed to type sets.

    Parameters
    ----------
    solution : { TypeVar : set([ Type ]) }
        Typing solution

    Returns: { TypeVar : Type }
        Returns a solution reduced to concrete types only.
    """
    if S is None:
        S = IdentityDict()

    for typevar, t in dict_iteritems(solution):
        if typevar in S:
            continue

        typeset = solution[typevar]
        freevars = IdentityDict.fromkeys(chain(*[free(t) for t in typeset]))

        if not typeset:
            S[typevar] = typevar
            typeset.add(typevar)
            continue
        elif freevars:
            # Reify dependencies first
            reify(dict((v, solution[v]) for v in freevars), S)
            typeset = set(substitute(S, t) for t in typeset)

        S[typevar] = promote_units(*typeset)

    return S

#------------------------------------------------------------------------
# Substitution
#------------------------------------------------------------------------

class Substitutor(object):
    def __init__(self, solution):
        self.solution = solution

    def TypeVar(self, typevar):
        return self.solution[typevar] or typevar

def substitute(solution, ds):
    """
    Substitute a typing solution for a type, resolving all free type variables.
    """
    return transform(Substitutor(solution), ds)


if __name__ == '__main__':
    import doctest
    doctest.testmod()