"""Rule registry.

Module names mirror rule IDs (snake_case ↔ kebab-case): the file
`hex_short.py` defines `HexShortRule` with `id = 'hex-short'`. No
tier prefix in the filename — fix-safety lives on the class
(`cls.fix_tier`) and the tier-grouped constants below
(`TIER_0_RULES`, …) drive things that need to know.
"""

from __future__ import annotations

from ._base import LintContext, Rule
from .ambiguous_math import AmbiguousMathRule
from .blank_line_at_block_end import BlankLineAtBlockEndRule
from .blank_line_at_block_start import BlankLineAtBlockStartRule
from .blank_line_before_block import BlankLineBeforeBlockRule
from .block_closing_brace_newline_after import BlockClosingBraceNewlineAfterRule
from .block_opening_brace_newline_after import BlockOpeningBraceNewlineAfterRule
from .brace_placement import (
    BlockOpeningBraceLineRule,
    ClosingBraceNewlineBeforeRule,
)
from .confusing_default_value import ConfusingDefaultValueRule
from .decimal_leading_zero import DecimalLeadingZeroRule
from .decls_before_rulesets import DeclsBeforeRulesetsRule
from .deep_nesting import DeepNestingRule
from .duplicate_property import DuplicatePropertyRule
from .excessive_mixin_args import ExcessiveMixinArgsRule
from .extend_cross_media import ExtendCrossMediaRule
from .final_newline import FinalNewlineRule
from .hex_case import HexCaseRule
from .hex_short import HexShortRule
from .important_overuse import ImportantOveruseRule
from .indent_consistency import IndentConsistencyRule
from .indent_width import IndentWidthRule
from .magic_number import MagicNumberRule
from .mixed_rest_and_default import MixedRestAndDefaultRule
from .no_lone_semicolon import NoLoneSemicolonRule
from .no_multiple_blank_lines import NoMultipleBlankLinesRule
from .no_space_after_lbrace import NoSpaceAfterLBraceRule
from .no_space_around_attr_eq import NoSpaceAroundAttrEqRule
from .no_space_before_rbrace import NoSpaceBeforeRBraceRule
from .no_tabs import NoTabsRule
from .quote_pref import QuotePrefRule
from .redefined_builtin import RedefinedBuiltinRule
from .redundant_mixin_args import RedundantMixinArgsRule
from .semicolon_newline_after import SemicolonNewlineAfterRule
from .semicolon_required import SemicolonRequiredRule
from .space_after_colon import SpaceAfterColonRule
from .space_around_binary_op import SpaceAroundBinaryOpRule
from .space_around_combinator import SpaceAroundCombinatorRule
from .space_before_lbrace import SpaceBeforeLBraceRule
from .trailing_whitespace import TrailingWhitespaceRule
from .trailing_zero import TrailingZeroRule
from .unreachable_branch import UnreachableMixinBranchRule
from .unsupported_feature import UnsupportedFeatureRule
from .unused_mixin import UnusedMixinRule
from .unused_variable import UnusedVariableRule
from .zero_unit import ZeroUnitRule

TIER_0_RULES: tuple[type[Rule], ...] = (
    TrailingWhitespaceRule,
    FinalNewlineRule,
    NoTabsRule,
    IndentWidthRule,
    IndentConsistencyRule,
    NoMultipleBlankLinesRule,
    SpaceAfterColonRule,
    SpaceBeforeLBraceRule,
    NoSpaceAfterLBraceRule,
    NoSpaceBeforeRBraceRule,
    SpaceAroundCombinatorRule,
    SpaceAroundBinaryOpRule,
    NoSpaceAroundAttrEqRule,
    SemicolonRequiredRule,
    SemicolonNewlineAfterRule,
    NoLoneSemicolonRule,
    QuotePrefRule,
    BlankLineAtBlockStartRule,
    BlankLineAtBlockEndRule,
    BlankLineBeforeBlockRule,
    BlockOpeningBraceLineRule,
    BlockOpeningBraceNewlineAfterRule,
    BlockClosingBraceNewlineAfterRule,
    ClosingBraceNewlineBeforeRule,
    DeclsBeforeRulesetsRule,
)

TIER_1_RULES: tuple[type[Rule], ...] = (
    HexShortRule,
    HexCaseRule,
    ZeroUnitRule,
    DecimalLeadingZeroRule,
    TrailingZeroRule,
)

TIER_2_RULES: tuple[type[Rule], ...] = (
    UnusedVariableRule,
    UnusedMixinRule,
    DuplicatePropertyRule,
    RedundantMixinArgsRule,
)

TIER_3_RULES: tuple[type[Rule], ...] = (
    DeepNestingRule,
    UnsupportedFeatureRule,
    MagicNumberRule,
    ImportantOveruseRule,
    ExcessiveMixinArgsRule,
    ConfusingDefaultValueRule,
    MixedRestAndDefaultRule,
    ExtendCrossMediaRule,
    AmbiguousMathRule,
    RedefinedBuiltinRule,
    UnreachableMixinBranchRule,
)

ALL_RULES: tuple[type[Rule], ...] = (
    *TIER_0_RULES,
    *TIER_1_RULES,
    *TIER_2_RULES,
    *TIER_3_RULES,
)


def rule_by_id(rule_id: str) -> type[Rule] | None:
    for cls in ALL_RULES:
        if cls.id == rule_id:
            return cls
    return None


__all__ = [
    'ALL_RULES',
    'TIER_0_RULES',
    'TIER_1_RULES',
    'TIER_2_RULES',
    'TIER_3_RULES',
    'LintContext',
    'Rule',
    'rule_by_id',
]
