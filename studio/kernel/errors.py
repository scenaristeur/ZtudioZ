"""Erreurs du noyau studio."""


class StudioError(Exception):
    """Erreur de base du runtime studio."""


class LoadError(StudioError):
    """Document YAML-LD invalide ou reference morte."""


class GraphError(StudioError):
    """Graphe de workflow invalide."""


class GateError(StudioError):
    """Reponse de gate invalide."""


class BudgetError(StudioError):
    """Budget de noeud depasse avant appel."""


class SchemaError(StudioError):
    """Sortie d'agent non conforme a son schema."""
