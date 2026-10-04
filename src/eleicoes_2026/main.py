from __future__ import annotations

import base64
import binascii
import json
import os
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


TSE_BASE_URL = "https://resultados.tse.jus.br/oficial"
TSE_CONFIG_URL = f"{TSE_BASE_URL}/comum/config/ele-c.jws"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = PROJECT_ROOT / "static"
CONFIG_CACHE_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 15

_config_cache: tuple[float, dict[str, Any]] | None = None
_config_cache_lock = threading.Lock()
_locations_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_locations_cache_lock = threading.Lock()


class TSEDataError(Exception):
    """A API do TSE falhou ou retornou dados em formato inesperado."""


class TSESelectionError(TSEDataError):
    """A seleção de cargo ou localidade não é válida para esta eleição."""


def decode_jws_payload(token: str) -> dict[str, Any]:
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise TSEDataError("O TSE retornou um arquivo assinado em formato inválido.")

    encoded_payload = parts[1]
    padded_payload = encoded_payload + "=" * (-len(encoded_payload) % 4)
    try:
        payload_bytes = base64.b64decode(
            padded_payload, altchars=b"-_", validate=True
        )
        payload = json.loads(payload_bytes)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TSEDataError("Não foi possível interpretar os dados assinados do TSE.") from error

    if not isinstance(payload, dict):
        raise TSEDataError("O TSE retornou dados em formato inesperado.")
    return payload


def fetch_tse_payload(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "Eleicoes-2026/1.0"})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            token = response.read().decode("utf-8")
    except HTTPError as error:
        raise TSEDataError(
            f"O TSE respondeu com HTTP {error.code} ao consultar os resultados."
        ) from error
    except (URLError, TimeoutError) as error:
        raise TSEDataError(f"Não foi possível acessar a API do TSE: {error}") from error
    except UnicodeDecodeError as error:
        raise TSEDataError("O TSE retornou uma resposta que não está em UTF-8.") from error

    return decode_jws_payload(token)


def get_tse_config() -> dict[str, Any]:
    global _config_cache

    with _config_cache_lock:
        now = time.monotonic()
        if _config_cache is None or now - _config_cache[0] >= CONFIG_CACHE_SECONDS:
            config = fetch_tse_payload(TSE_CONFIG_URL)
            _config_cache = (time.monotonic(), config)
        else:
            config = _config_cache[1]
    return config


def get_elections() -> list[dict[str, Any]]:
    elections: list[dict[str, Any]] = []
    for group in get_tse_config().get("pl", []):
        if not isinstance(group, dict) or group.get("c") != "ele2026":
            continue
        for election in group.get("e", []):
            if not isinstance(election, dict) or election.get("tp") not in {"8", "1"}:
                continue
            cargos = [
                cargo
                for region in election.get("abr", [])
                if isinstance(region, dict)
                for cargo in region.get("cp", [])
                if isinstance(cargo, dict)
                and str(cargo.get("cd")) in {"1", "3", "5", "6", "7", "8"}
            ]
            if not cargos:
                continue
            elections.append(
                {
                    "id": str(election["cd"]),
                    "cycle": group["c"],
                    "name": election["nm"],
                    "date": group.get("dt", ""),
                    "turn": election.get("t", ""),
                    "type": election["tp"],
                    "cargos": [
                        {"code": str(cargo["cd"]), "name": cargo["ds"]}
                        for cargo in cargos
                    ],
                }
            )
    return elections


def get_locations(election: dict[str, Any]) -> list[dict[str, Any]]:
    election_id = election["id"]
    now = time.monotonic()
    with _locations_cache_lock:
        cached = _locations_cache.get(election_id)
        if cached is not None and now - cached[0] < CONFIG_CACHE_SECONDS:
            return cached[1]

    url = (
        f"{TSE_BASE_URL}/{election['cycle']}/{election_id}/config/"
        f"mun-e{election_id.zfill(6)}-cm.jws"
    )
    payload = fetch_tse_payload(url)
    regions = payload.get("abr")
    if not isinstance(regions, list):
        raise TSEDataError("A configuração de estados e municípios do TSE está incompleta.")

    locations = [
        region
        for region in regions
        if isinstance(region, dict)
        and isinstance(region.get("cd"), str)
        and isinstance(region.get("mu"), list)
    ]
    if not locations:
        raise TSEDataError("O TSE ainda não disponibilizou os municípios desta eleição.")

    with _locations_cache_lock:
        _locations_cache[election_id] = (time.monotonic(), locations)
    return locations


def result_url(
    election: dict[str, Any],
    cargo_code: str,
    state_code: str,
    municipality_code: str | None = None,
) -> str:
    election_id = election["id"]
    state_code = state_code.lower()
    locality_prefix = state_code
    if municipality_code is not None:
        locality_prefix += municipality_code.zfill(5)
    return (
        f"{TSE_BASE_URL}/{election['cycle']}/{election_id}/dados/{state_code}/"
        f"{locality_prefix}-c{cargo_code.zfill(4)}-e{election_id.zfill(6)}-u.jws"
    )


def parse_integer(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise TSEDataError(f"O campo {field_name} enviado pelo TSE é inválido.") from error


def parse_optional_integer(value: Any, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    return parse_integer(value, field_name)


def extract_candidates(
    cargo: dict[str, Any],
    valid_votes: int | None = None,
    turn: str = "1",
    qualification_cargo: dict[str, Any] | None = None,
    qualification_votes: int | None = None,
    photo_url_prefix: str | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seat_ids: set[str] = set()
    highlight_statuses: dict[str, str] = {}
    proportional = str(cargo.get("cd")) in {"6", "7", "8"}
    majoritarian_with_runoff = str(cargo.get("cd")) in {"1", "3"}

    if majoritarian_with_runoff:
        qualification_source = qualification_cargo or cargo
        votes_for_qualification = (
            qualification_votes if qualification_votes is not None else valid_votes
        )
        eligible = [
            candidate
            for group in qualification_source.get("agr", [])
            if isinstance(group, dict)
            for party in group.get("par", [])
            if isinstance(party, dict)
            for candidate in party.get("cand", [])
            if isinstance(candidate, dict)
            and candidate.get("dvt", "Válido") == "Válido"
        ]
        eligible.sort(
            key=lambda candidate: (
                -parse_integer(candidate.get("vap"), "cand.vap"),
                _candidate_birth_date(candidate),
                parse_integer(candidate.get("seq", 0), "cand.seq"),
            )
        )
        if votes_for_qualification is not None and votes_for_qualification > 0 and eligible:
            leading_candidate = eligible[0]
            if (
                parse_integer(leading_candidate.get("vap"), "cand.vap") * 2
                > votes_for_qualification
            ):
                highlight_statuses[_candidate_id(leading_candidate)] = "majority"
            elif turn != "2" and len(eligible) >= 2:
                for candidate in eligible[:2]:
                    highlight_statuses[_candidate_id(candidate)] = "second_round"
    elif proportional:
        minimum_votes = (
            (parse_integer(cargo["qe"], "carg.qe") + 9) // 10
            if cargo.get("qe")
            else 0
        )
        for group in cargo.get("agr", []):
            if not isinstance(group, dict):
                continue
            seats = parse_integer(group.get("vag", 0), "agr.vag")
            eligible = [
                candidate
                for party in group.get("par", [])
                if isinstance(party, dict)
                for candidate in party.get("cand", [])
                if isinstance(candidate, dict)
                and candidate.get("dvt", "Válido") == "Válido"
                and parse_integer(candidate.get("vap"), "cand.vap") >= minimum_votes
            ]
            eligible.sort(
                key=lambda candidate: (
                    -parse_integer(candidate.get("vap"), "cand.vap"),
                    parse_integer(candidate.get("seq", 0), "cand.seq"),
                )
            )
            seat_ids.update(
                str(candidate.get("sqcand") or candidate.get("n"))
                for candidate in eligible[:seats]
            )
    else:
        seats = parse_integer(cargo.get("nv", 0), "carg.nv")
        eligible = [
            candidate
            for group in cargo.get("agr", [])
            if isinstance(group, dict)
            for party in group.get("par", [])
            if isinstance(party, dict)
            for candidate in party.get("cand", [])
            if isinstance(candidate, dict)
            and candidate.get("dvt", "Válido") == "Válido"
            and parse_integer(candidate.get("vap"), "cand.vap") > 0
        ]
        eligible.sort(
            key=lambda candidate: (
                -parse_integer(candidate.get("vap"), "cand.vap"),
                parse_integer(candidate.get("seq", 0), "cand.seq"),
            )
        )
        seat_ids.update(
            str(candidate.get("sqcand") or candidate.get("n"))
            for candidate in eligible[:seats]
        )

    for group in cargo.get("agr", []):
        if not isinstance(group, dict):
            continue
        for party in group.get("par", []):
            if not isinstance(party, dict):
                continue
            for candidate in party.get("cand", []):
                if not isinstance(candidate, dict):
                    continue
                officially_elected = (
                    str(candidate.get("e", "")).strip().lower() == "s"
                )
                candidate_id = _candidate_id(candidate)
                sequence = str(candidate.get("sqcand", "")).strip()
                candidates.append(
                    {
                        "number": str(candidate.get("n", "")),
                        "name": str(candidate.get("nmu") or candidate.get("nm") or ""),
                        "party": str(party.get("sg") or group.get("com") or ""),
                        "photoUrl": (
                            f"{photo_url_prefix}{sequence}.jpeg"
                            if photo_url_prefix and sequence.isdigit()
                            else None
                        ),
                        "votes": parse_integer(candidate.get("vap"), "vap"),
                        "percentage": str(candidate.get("pvap", "0,00")),
                        "order": parse_integer(candidate.get("seq", 0), "seq"),
                        "inSeat": (
                            officially_elected
                            or candidate_id in seat_ids
                            or candidate_id in highlight_statuses
                        ),
                        "highlightStatus": (
                            "elected"
                            if officially_elected
                            else highlight_statuses.get(
                                candidate_id,
                                "seat" if candidate_id in seat_ids else None,
                            )
                        ),
                        "officiallyElected": officially_elected,
                    }
                )

    candidates.sort(key=lambda candidate: (-candidate["votes"], candidate["order"]))
    return candidates


def _candidate_id(candidate: dict[str, Any]) -> str:
    return str(candidate.get("sqcand") or candidate.get("n"))


def _candidate_birth_date(candidate: dict[str, Any]) -> datetime:
    birth_date = candidate.get("dt", "")
    try:
        return datetime.strptime(birth_date, "%d/%m/%Y")
    except (TypeError, ValueError):
        return datetime.max


def find_cargo(election: dict[str, Any], cargo_code: str) -> dict[str, str] | None:
    return next(
        (cargo for cargo in election["cargos"] if cargo["code"] == cargo_code),
        None,
    )


def make_result_response(
    election: dict[str, Any],
    cargo_code: str,
    state_code: str,
    municipality_code: str | None = None,
) -> dict[str, Any]:
    cargo_info = find_cargo(election, cargo_code)
    if cargo_info is None:
        raise TSESelectionError("O cargo selecionado não está disponível nesta eleição.")

    locations = get_locations(election)
    if state_code.lower() == "br" and election["type"] == "8":
        if municipality_code is not None:
            raise TSESelectionError("Escolha um estado antes de filtrar por município.")
        state = {"cd": "br", "ds": "Brasil", "mu": []}
    else:
        state = next(
            (
                region
                for region in locations
                if region["cd"].lower() == state_code.lower()
            ),
            None,
        )
    if state is None:
        raise TSESelectionError("O estado selecionado não está disponível nesta eleição.")

    municipality_name = None
    if municipality_code is not None:
        municipality = next(
            (
                item
                for item in state["mu"]
                if str(item.get("cd")) == municipality_code
            ),
            None,
        )
        if municipality is None:
            raise TSESelectionError(
                "O município selecionado não pertence ao estado informado."
            )
        municipality_name = str(municipality.get("nm", ""))

    if cargo_code == "8" and state_code.lower() != "df":
        raise TSESelectionError(
            "Deputado distrital só está disponível no Distrito Federal."
        )
    if cargo_code == "7" and state_code.lower() == "df":
        raise TSESelectionError(
            "Deputado estadual não é um cargo disputado no Distrito Federal."
        )

    payload = fetch_tse_payload(
        result_url(election, cargo_code, state_code, municipality_code)
    )
    cargos = payload.get("carg")
    cargo_result = next(
        (
            item
            for item in cargos
            if isinstance(item, dict) and str(item.get("cd")) == cargo_code
        ),
        None,
    ) if isinstance(cargos, list) else None
    if cargo_result is None:
        raise TSEDataError("O arquivo do TSE ainda não contém resultados para este cargo.")

    sections = payload.get("s")
    votes = payload.get("v")
    if not isinstance(sections, dict) or not isinstance(votes, dict):
        raise TSEDataError("O arquivo de resultados do TSE está incompleto.")

    valid_votes = parse_integer(votes.get("vvc"), "v.vvc")
    majoritarian_with_runoff = cargo_code in {"1", "3"}
    qualification_cargo = cargo_result
    qualification_votes = valid_votes
    qualification_state = "br" if cargo_code == "1" else state_code
    if majoritarian_with_runoff and (
        state_code.lower() != qualification_state.lower()
        or municipality_code is not None
    ):
        qualification_payload = fetch_tse_payload(
            result_url(election, cargo_code, qualification_state)
        )
        qualification_cargos = qualification_payload.get("carg")
        qualification_cargo = next(
            (
                item
                for item in qualification_cargos
                if isinstance(item, dict) and str(item.get("cd")) == cargo_code
            ),
            None,
        ) if isinstance(qualification_cargos, list) else None
        qualification_votes_section = qualification_payload.get("v")
        if qualification_cargo is None or not isinstance(
            qualification_votes_section, dict
        ):
            raise TSEDataError(
                "O TSE não retornou dados suficientes para calcular a classificação "
                "do segundo turno."
            )
        qualification_votes = parse_integer(
            qualification_votes_section.get("vvc"), "v.vvc"
        )

    candidates = extract_candidates(
        cargo_result,
        valid_votes,
        election["turn"],
        qualification_cargo,
        qualification_votes,
        (
            f"{TSE_BASE_URL}/{election['cycle']}/{election['id']}/fotos/"
            f"{'br' if election['type'] == '8' else state_code.lower()}/"
        ),
    )
    elected_candidates = [
        candidate for candidate in candidates if candidate["officiallyElected"]
    ]
    if majoritarian_with_runoff:
        leading_candidate = next(
            (
                candidate
                for candidate in candidates
                if candidate["highlightStatus"] == "majority"
            ),
            None,
        )
        qualification_location = (
            "Brasil"
            if cargo_code == "1"
            else state.get("ds", state_code.upper())
        )
        qualification_scope = (
            f"Classificação calculada para {qualification_location}"
            + (
                ", independentemente da cidade selecionada."
                if municipality_code is not None
                else "."
            )
        )
        if str(election["turn"]) == "2":
            highlight_basis = (
                f"{qualification_scope} No segundo turno, vence quem obtiver "
                "maioria dos votos válidos, sujeita à totalização oficial."
            )
            highlight_label = "Maioria absoluta no 2º turno"
        elif qualification_votes == 0:
            highlight_basis = (
                f"{qualification_scope} Aguardando votos válidos; serão "
                "destacados os dois mais votados se ninguém alcançar mais de "
                "50% dos votos válidos."
            )
            highlight_label = "Destaque: disputa do 2º turno"
        elif leading_candidate is not None:
            highlight_basis = (
                f"{qualification_scope} Mais de 50% dos votos válidos; a maioria "
                "absoluta pode evitar o 2º turno, sujeita à totalização oficial."
            )
            highlight_label = "Maioria absoluta (>50% dos votos válidos)"
        else:
            highlight_basis = (
                f"{qualification_scope} Se ninguém alcançar mais de 50% dos "
                "votos válidos, os dois mais votados disputarão o 2º turno."
            )
            highlight_label = "Destaque: dois mais votados para o 2º turno"
    else:
        highlight_basis = (
            "Vagas atribuídas pelo TSE às legendas e votação individual mínima"
            if cargo_code in {"6", "7", "8"}
            else "Maiores votações para o cargo"
        )
        highlight_label = "Destaque: dentro das vagas"

    if elected_candidates:
        highlight_basis = (
            "Marcação oficial de candidato eleito publicada pelo TSE; os demais "
            "destaques continuam sendo estimativas da apuração."
        )
        highlight_label = "Eleição confirmada pelo TSE"

    locality_name = (
        "Brasil"
        if state_code.lower() == "br"
        else state.get("ds", state_code.upper())
    )
    if municipality_name:
        locality_name = f"{municipality_name} / {locality_name}"

    return {
        "election": election["name"],
        "cargo": cargo_info["name"],
        "location": locality_name,
        "lastUpdated": " ".join(
            value for value in (payload.get("dg", ""), payload.get("hg", "")) if value
        ),
        "sections": {
            "totalized": parse_integer(sections.get("st"), "s.st"),
            "total": parse_integer(sections.get("ts"), "s.ts"),
            "percentage": str(sections.get("pst", "0,00")),
        },
        "votes": {
            "valid": valid_votes,
            "total": parse_integer(votes.get("tv"), "v.tv"),
            "blank": parse_optional_integer(votes.get("vb"), "v.vb"),
            "null": parse_optional_integer(votes.get("tvn"), "v.tvn"),
        },
        "candidates": candidates,
        "vacancies": parse_integer(cargo_result.get("nv", 0), "carg.nv"),
        "highlightBasis": highlight_basis,
        "highlightLabel": highlight_label,
        "officialUrl": (
            "https://resultados.tse.jus.br/oficial/app/index.html"
            f"#/eleicao/{election['id']}/uf/{state_code.lower()}/cargo/{cargo_code}"
            "/vis/nominal/resultados"
        ),
    }


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        try:
            self.dispatch_request()
        except TSESelectionError as error:
            self.respond_json({"error": str(error)}, status=400)
        except TSEDataError as error:
            self.log_error("Falha na consulta à API do TSE: %s", error)
            self.respond_json({"error": str(error)}, status=502)

    def dispatch_request(self) -> None:
        request_url = urlparse(self.path)
        if request_url.path == "/":
            self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif request_url.path == "/api/elections":
            self.respond_json({"elections": get_elections()})
        elif request_url.path == "/api/locations":
            self.serve_locations(parse_qs(request_url.query))
        elif request_url.path == "/api/results":
            self.serve_results(parse_qs(request_url.query))
        else:
            self.respond_json({"error": "Página não encontrada."}, status=404)

    def find_election(self, election_ids: list[str]) -> dict[str, Any] | None:
        if len(election_ids) != 1:
            return None
        return next(
            (item for item in get_elections() if item["id"] == election_ids[0]),
            None,
        )

    def serve_locations(self, query: dict[str, list[str]]) -> None:
        election_ids = query.get("election", [])
        if len(election_ids) != 1:
            self.respond_json({"error": "Informe uma eleição válida."}, status=400)
            return

        election = self.find_election(election_ids)
        if election is None:
            self.respond_json(
                {"error": "A eleição solicitada não está disponível na configuração do TSE."},
                status=404,
            )
            return

        locations = get_locations(election)
        states = [
            {
                "code": location["cd"].lower(),
                "name": location.get("ds", location["cd"].upper()),
            }
            for location in locations
            if location["cd"].lower() != "br"
        ]
        state_codes = query.get("state", [])
        if not state_codes:
            self.respond_json({"states": states})
            return
        if len(state_codes) != 1:
            self.respond_json({"error": "Informe um estado válido."}, status=400)
            return

        state = next(
            (
                location
                for location in locations
                if location["cd"].lower() == state_codes[0].lower()
            ),
            None,
        )
        if state is None:
            self.respond_json(
                {"error": "O estado solicitado não está disponível."}, status=404
            )
            return

        municipalities = [
            {"code": str(municipality["cd"]), "name": municipality["nm"]}
            for municipality in state["mu"]
            if isinstance(municipality, dict)
            and isinstance(municipality.get("cd"), str)
            and isinstance(municipality.get("nm"), str)
        ]
        municipalities.sort(key=lambda municipality: municipality["name"])
        self.respond_json({"states": states, "municipalities": municipalities})

    def serve_results(self, query: dict[str, list[str]]) -> None:
        required = ("election", "cargo", "state")
        if any(len(query.get(key, [])) != 1 for key in required):
            self.respond_json(
                {"error": "Informe uma eleição, um cargo e um estado válidos."},
                status=400,
            )
            return

        election = self.find_election(query["election"])
        if election is None:
            self.respond_json(
                {"error": "A eleição solicitada não está disponível na configuração do TSE."},
                status=404,
            )
            return

        if len(query.get("municipality", [])) > 1:
            self.respond_json({"error": "Informe um único município."}, status=400)
            return
        municipality_code = query.get("municipality", [None])[0]
        self.respond_json(
            make_result_response(
                election,
                query["cargo"][0],
                query["state"][0],
                municipality_code,
            )
        )

    def serve_file(self, path: Path, content_type: str) -> None:
        try:
            content = path.read_bytes()
        except OSError:
            self.respond_json({"error": "A interface do painel não foi encontrada."}, status=500)
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def respond_json(self, data: dict[str, Any], status: int = 200) -> None:
        try:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as error:
            body = json.dumps(
                {"error": f"Não foi possível preparar a resposta: {error}"},
                ensure_ascii=False,
            ).encode("utf-8")
            status = 500

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format_string % args}")


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    if not 1 <= port <= 65535:
        raise ValueError("A porta do servidor deve estar entre 1 e 65535.")

    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"Painel disponível na porta {port}.")
    print(f"Neste computador, abra http://127.0.0.1:{port}")
    print("Pressione Ctrl+C para encerrar.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando o painel.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
