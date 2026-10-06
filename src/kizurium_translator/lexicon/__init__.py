"""Пакет словарей."""

from .regex_rules import RegexRule, apply_regex, load_regex_rules, validate_pattern
from .store import Groups, Term, compile_index, exact, groups, load_terms

__all__ = [
    "Groups",
    "RegexRule",
    "Term",
    "apply_regex",
    "compile_index",
    "exact",
    "groups",
    "load_regex_rules",
    "load_terms",
    "validate_pattern",
]
