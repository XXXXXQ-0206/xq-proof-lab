"""Candidate ordering and external-engine adapters.

These modules may accelerate search, but their outputs are never proof results.
"""

from .uci_engine import (
    PerftResult,
    UciEngine,
    UciEngineError,
    extract_go_searchmoves,
    extract_perft_divide,
    extract_pv_moves,
    split_engine_command,
    starts_with_uci_token,
)
from .chessdb import (
    ChessDbAction,
    ChessDbClient,
    ChessDbMove,
    ChessDbResponse,
    ChessDbRuleQuery,
    ChessDbRuleResult,
    ChessDbStatus,
    build_rule_query_from_state,
    parse_chessdb_response,
    parse_query_all_moves,
    parse_query_rule_results,
)
from .move_ordering import (
    CachedMoveOrderer,
    ChessDbMoveOrderer,
    HeuristicMoveOrderer,
    LexicographicMoveOrderer,
    PersistentUciBestMoveOrderer,
    UciBestMoveOrderer,
    parse_uci_options,
)
from .local_search import LocalSearchMoveOrderer

__all__ = [
    "ChessDbAction",
    "ChessDbClient",
    "ChessDbMove",
    "ChessDbResponse",
    "ChessDbRuleQuery",
    "ChessDbRuleResult",
    "ChessDbStatus",
    "CachedMoveOrderer",
    "ChessDbMoveOrderer",
    "HeuristicMoveOrderer",
    "LexicographicMoveOrderer",
    "LocalSearchMoveOrderer",
    "PersistentUciBestMoveOrderer",
    "UciBestMoveOrderer",
    "parse_uci_options",
    "PerftResult",
    "UciEngine",
    "UciEngineError",
    "extract_go_searchmoves",
    "extract_perft_divide",
    "extract_pv_moves",
    "split_engine_command",
    "starts_with_uci_token",
    "build_rule_query_from_state",
    "parse_chessdb_response",
    "parse_query_all_moves",
    "parse_query_rule_results",
]
