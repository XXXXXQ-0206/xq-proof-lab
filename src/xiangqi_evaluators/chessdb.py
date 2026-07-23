from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


class ChessDbAction(str, Enum):
    QUERY_ALL = "queryall"
    QUERY_BEST = "querybest"
    QUERY_SCORE = "queryscore"
    QUERY_PV = "querypv"
    QUERY_RULE = "queryrule"


class ChessDbStatus(str, Enum):
    OK = "ok"
    UNKNOWN = "unknown"
    CHECKMATE = "checkmate"
    STALEMATE = "stalemate"
    NO_BEST_MOVE = "nobestmove"
    INVALID_BOARD = "invalid_board"
    INVALID_MOVELIST = "invalid_movelist"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ChessDbMove:
    move: str
    score: int | None = None
    rank: int | None = None
    winrate: float | None = None
    note: str | None = None
    raw: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class ChessDbRuleResult:
    move: str
    rule: str
    raw: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class ChessDbRuleQuery:
    fen: str
    movelist: tuple[str, ...]
    reptimes: int | None = None


@dataclass(frozen=True, slots=True)
class ChessDbResponse:
    status: ChessDbStatus
    raw_text: str
    moves: tuple[ChessDbMove, ...] = ()
    best_move: str | None = None
    best_move_source: str | None = None
    score: int | None = None
    pv: tuple[str, ...] = ()
    rule: str | None = None
    rule_results: tuple[ChessDbRuleResult, ...] = ()
    error: str | None = None


class ChessDbClient:
    def __init__(
        self,
        base_url: str = "https://www.chessdb.cn/chessdb.php",
        timeout: float = 8.0,
        learn: bool = False,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.learn = learn

    def build_url(self, action: ChessDbAction, fen: str, **params: Any) -> str:
        query = {
            "action": action.value,
            "board": fen,
            "learn": int(self.learn),
        }
        query.update({key: value for key, value in params.items() if value is not None})
        return f"{self.base_url}?{urlencode(query)}"

    def query_best(
        self,
        fen: str,
        *,
        egtbmetric: str | None = None,
        ban: tuple[str, ...] | list[str] = (),
    ) -> ChessDbResponse:
        return self._query(
            ChessDbAction.QUERY_BEST,
            fen,
            egtbmetric=egtbmetric,
            ban="|".join(ban) if ban else None,
        )

    def query_all(
        self,
        fen: str,
        showall: bool = False,
        *,
        egtbmetric: str | None = None,
        ban: tuple[str, ...] | list[str] = (),
    ) -> ChessDbResponse:
        return self._query(
            ChessDbAction.QUERY_ALL,
            fen,
            showall=int(showall),
            egtbmetric=egtbmetric,
            ban="|".join(ban) if ban else None,
        )

    def query_score(self, fen: str, *, egtbmetric: str | None = None) -> ChessDbResponse:
        return self._query(ChessDbAction.QUERY_SCORE, fen, egtbmetric=egtbmetric)

    def query_pv(self, fen: str, *, egtbmetric: str | None = None) -> ChessDbResponse:
        return self._query(ChessDbAction.QUERY_PV, fen, egtbmetric=egtbmetric)

    def query_rule(
        self,
        fen: str,
        movelist: tuple[str, ...] | list[str] = (),
        reptimes: int | None = None,
    ) -> ChessDbResponse:
        return self._query(
            ChessDbAction.QUERY_RULE,
            fen,
            movelist="|".join(movelist) if movelist else None,
            reptimes=reptimes,
        )

    def _query(self, action: ChessDbAction, fen: str, **params: Any) -> ChessDbResponse:
        url = self.build_url(action, fen, **params)
        with urlopen(url, timeout=self.timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
        return parse_chessdb_response(text, action)


def parse_chessdb_response(text: str, action: ChessDbAction | str) -> ChessDbResponse:
    parsed_action = action if isinstance(action, ChessDbAction) else ChessDbAction(action)
    clean = text.replace("\x00", "").strip()
    lowered = clean.lower()

    if not clean:
        return ChessDbResponse(ChessDbStatus.ERROR, clean, error="empty response")
    if lowered == "unknown":
        return ChessDbResponse(ChessDbStatus.UNKNOWN, clean)
    if lowered == "checkmate":
        return ChessDbResponse(ChessDbStatus.CHECKMATE, clean)
    if lowered == "stalemate":
        return ChessDbResponse(ChessDbStatus.STALEMATE, clean)
    if lowered == "nobestmove":
        return ChessDbResponse(ChessDbStatus.NO_BEST_MOVE, clean)
    if lowered == "invalid board":
        return ChessDbResponse(ChessDbStatus.INVALID_BOARD, clean, error=clean)
    if lowered == "invalid movelist":
        return ChessDbResponse(ChessDbStatus.INVALID_MOVELIST, clean, error=clean)
    if lowered.startswith("error"):
        return ChessDbResponse(ChessDbStatus.ERROR, clean, error=clean)

    if parsed_action is ChessDbAction.QUERY_ALL:
        return ChessDbResponse(ChessDbStatus.OK, clean, moves=parse_query_all_moves(clean))

    fields = _parse_fields(clean)
    if parsed_action is ChessDbAction.QUERY_BEST:
        best_move, source = _best_move_from_fields(fields)
        return ChessDbResponse(
            ChessDbStatus.OK,
            clean,
            best_move=best_move,
            best_move_source=source,
        )
    if parsed_action is ChessDbAction.QUERY_SCORE:
        return ChessDbResponse(
            ChessDbStatus.OK,
            clean,
            score=_int_or_none(fields.get("score") or fields.get("eval")),
        )
    if parsed_action is ChessDbAction.QUERY_PV:
        pv_text = fields.get("pv", "")
        return ChessDbResponse(ChessDbStatus.OK, clean, pv=tuple(pv_text.split()))
    if parsed_action is ChessDbAction.QUERY_RULE:
        rule_results = parse_query_rule_results(clean)
        return ChessDbResponse(
            ChessDbStatus.OK,
            clean,
            rule=rule_results[0].rule if len(rule_results) == 1 else None,
            rule_results=rule_results,
        )

    return ChessDbResponse(ChessDbStatus.OK, clean)


def parse_query_all_moves(text: str) -> tuple[ChessDbMove, ...]:
    moves: list[ChessDbMove] = []
    for chunk in text.split("|"):
        fields = _parse_fields(chunk)
        move = fields.get("move")
        if not move:
            continue
        moves.append(
            ChessDbMove(
                move=move,
                score=_int_or_none(fields.get("score")),
                rank=_int_or_none(fields.get("rank")),
                winrate=_float_or_none(fields.get("winrate")),
                note=fields.get("note"),
                raw=fields,
            )
        )
    return tuple(moves)


def parse_query_rule_results(text: str) -> tuple[ChessDbRuleResult, ...]:
    results: list[ChessDbRuleResult] = []
    for chunk in text.split("|"):
        fields = _parse_fields(chunk)
        move = fields.get("move")
        rule = fields.get("rule")
        if move is None or rule is None:
            continue
        results.append(ChessDbRuleResult(move=move, rule=rule, raw=fields))
    return tuple(results)


def _best_move_from_fields(fields: dict[str, str]) -> tuple[str | None, str | None]:
    for source in ("move", "egtb", "search"):
        move = fields.get(source)
        if move:
            return move, source
    return None, None


def build_rule_query_from_state(state) -> ChessDbRuleQuery:
    repetition_count = state.repetition_count()
    return ChessDbRuleQuery(
        fen=state.initial_fen,
        movelist=tuple(move.to_uci() for move in state.moves),
        reptimes=repetition_count - 1 if repetition_count >= 2 else None,
    )


def _parse_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in text.split(","):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        fields[key.strip().lower()] = value.strip()
    return fields


def _int_or_none(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _float_or_none(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None
