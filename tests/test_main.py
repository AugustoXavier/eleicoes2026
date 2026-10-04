import base64
import json
import os
import unittest
from unittest.mock import Mock, call, patch

from eleicoes_2026 import main


def make_jws(payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"header.{encoded}.signature"


def make_election(
    election_id: str = "6259",
    election_type: str = "1",
    cargos: list[dict[str, str]] | None = None,
) -> dict:
    return {
        "id": election_id,
        "cycle": "ele2026",
        "name": "Eleição Estadual 2026",
        "date": "04/10/2026",
        "turn": "1",
        "type": election_type,
        "cargos": cargos
        or [
            {"code": "3", "name": "Governador"},
            {"code": "5", "name": "Senador"},
            {"code": "6", "name": "Deputado Federal"},
            {"code": "7", "name": "Deputado Estadual"},
            {"code": "8", "name": "Deputado Distrital"},
        ],
    }


class JwsPayloadTests(unittest.TestCase):
    def test_decodes_signed_payload(self) -> None:
        expected = {"c": "ele2026", "value": 42}

        self.assertEqual(main.decode_jws_payload(make_jws(expected)), expected)

    def test_rejects_invalid_jws(self) -> None:
        with self.assertRaises(main.TSEDataError):
            main.decode_jws_payload("not-a-jws")


class ElectionConfigurationTests(unittest.TestCase):
    def test_server_uses_port_from_environment_and_listens_on_all_interfaces(self) -> None:
        server = Mock()
        with (
            patch.dict(os.environ, {"PORT": "9123"}),
            patch.object(main, "ThreadingHTTPServer", return_value=server) as create_server,
            patch("builtins.print"),
        ):
            main.main()

        create_server.assert_called_once_with(("0.0.0.0", 9123), main.DashboardHandler)
        server.serve_forever.assert_called_once()
        server.server_close.assert_called_once()

    def test_server_defaults_to_port_8000(self) -> None:
        server = Mock()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(main, "ThreadingHTTPServer", return_value=server) as create_server,
            patch("builtins.print"),
        ):
            main.main()

        create_server.assert_called_once_with(("0.0.0.0", 8000), main.DashboardHandler)

    def test_lists_presidential_and_state_elections_with_available_offices(self) -> None:
        config = {
            "pl": [
                {
                    "c": "ele2026",
                    "dt": "04/10/2026",
                    "e": [
                        {
                            "cd": "6257",
                            "nm": "Eleição Ordinária Federal - 2026 1º Turno",
                            "t": "1",
                            "tp": "8",
                            "abr": [
                                {
                                    "cd": "br",
                                    "cp": [{"cd": "1", "ds": "Presidente", "tp": "1"}],
                                }
                            ],
                        },
                        {
                            "cd": "6259",
                            "nm": "Eleição Ordinária Estadual - 2026 1º Turno",
                            "t": "1",
                            "tp": "1",
                            "abr": [
                                {
                                    "cd": "br",
                                    "cp": [
                                        {"cd": code, "ds": name, "tp": "1"}
                                        for code, name in [
                                            ("3", "Governador"),
                                            ("5", "Senador"),
                                            ("6", "Deputado Federal"),
                                            ("7", "Deputado Estadual"),
                                            ("8", "Deputado Distrital"),
                                        ]
                                    ],
                                }
                            ],
                        },
                        {
                            "cd": "6261",
                            "nm": "Eleição Municipal - 2026",
                            "tp": "3",
                            "abr": [{"cd": "br", "cp": [{"cd": "25", "ds": "Conselheiro"}]}],
                        },
                    ],
                }
            ]
        }

        with patch.object(main, "get_tse_config", return_value=config):
            elections = main.get_elections()

        self.assertEqual([election["id"] for election in elections], ["6257", "6259"])
        self.assertEqual(
            elections[0]["cargos"],
            [{"code": "1", "name": "Presidente"}],
        )
        self.assertEqual(
            [cargo["code"] for cargo in elections[1]["cargos"]],
            ["3", "5", "6", "7", "8"],
        )

    def test_builds_state_and_municipality_result_urls(self) -> None:
        election = make_election()

        self.assertEqual(
            main.result_url(election, "3", "sp"),
            "https://resultados.tse.jus.br/oficial/ele2026/6259/dados/sp/"
            "sp-c0003-e006259-u.jws",
        )
        self.assertEqual(
            main.result_url(election, "6", "sp", "71072"),
            "https://resultados.tse.jus.br/oficial/ele2026/6259/dados/sp/"
            "sp71072-c0006-e006259-u.jws",
        )

    def test_extracts_and_orders_candidates(self) -> None:
        cargo = {
            "cd": "1",
            "nv": "1",
            "agr": [
                {
                    "com": "Federação",
                    "par": [
                        {
                            "sg": "ABC",
                            "cand": [
                                {
                                    "n": "10",
                                    "nmu": "Candidato A",
                                    "vap": "35",
                                    "pvap": "35,00",
                                    "seq": "1",
                                    "sqcand": "candidate-a",
                                    "dvt": "Válido",
                                },
                                {
                                    "n": "20",
                                    "nmu": "Candidato B",
                                    "vap": "50",
                                    "pvap": "50,00",
                                    "seq": "2",
                                    "sqcand": "candidate-b",
                                    "dvt": "Válido",
                                },
                                {
                                    "n": "30",
                                    "nmu": "Candidato C",
                                    "vap": "15",
                                    "pvap": "15,00",
                                    "seq": "3",
                                    "sqcand": "candidate-c",
                                    "dvt": "Válido",
                                },
                            ],
                        }
                    ],
                }
            ]
        }

        self.assertEqual(
            [candidate["name"] for candidate in main.extract_candidates(cargo)],
            ["Candidato B", "Candidato A", "Candidato C"],
        )
        self.assertEqual(
            [
                candidate["inSeat"]
                for candidate in main.extract_candidates(cargo, valid_votes=100)
            ],
            [True, True, False],
        )

    def test_majority_requires_more_than_half_of_valid_votes(self) -> None:
        cargo = {
            "cd": "3",
            "nv": "1",
            "agr": [
                {
                    "par": [
                        {
                            "sg": "ABC",
                            "cand": [
                                {
                                    "n": "10",
                                    "sqcand": "candidate-a",
                                    "nmu": "Candidato A",
                                    "vap": "50",
                                    "pvap": "50,00",
                                    "seq": "1",
                                    "dvt": "Válido",
                                },
                                {
                                    "n": "20",
                                    "sqcand": "candidate-b",
                                    "nmu": "Candidato B",
                                    "vap": "35",
                                    "pvap": "35,00",
                                    "seq": "2",
                                    "dvt": "Válido",
                                },
                                {
                                    "n": "30",
                                    "sqcand": "candidate-c",
                                    "nmu": "Candidato C",
                                    "vap": "15",
                                    "pvap": "15,00",
                                    "seq": "3",
                                    "dvt": "Válido",
                                },
                            ],
                        }
                    ]
                }
            ],
        }

        candidates = main.extract_candidates(cargo, valid_votes=100)

        self.assertEqual(
            [candidate["highlightStatus"] for candidate in candidates],
            ["second_round", "second_round", None],
        )
        self.assertFalse(
            any(
                candidate["inSeat"]
                for candidate in main.extract_candidates(
                    cargo, valid_votes=100, turn="2"
                )
            )
        )

        cargo["agr"][0]["par"][0]["cand"][0]["vap"] = "51"
        candidates = main.extract_candidates(cargo, valid_votes=100)
        self.assertEqual(
            [candidate["highlightStatus"] for candidate in candidates],
            ["majority", None, None],
        )

    def test_tied_second_place_uses_older_candidate(self) -> None:
        cargo = {
            "cd": "1",
            "agr": [
                {
                    "par": [
                        {
                            "sg": "ABC",
                            "cand": [
                                {
                                    "n": "10",
                                    "sqcand": "candidate-a",
                                    "nmu": "Líder",
                                    "dt": "01/01/1980",
                                    "vap": "40",
                                    "seq": "1",
                                    "dvt": "Válido",
                                },
                                {
                                    "n": "20",
                                    "sqcand": "candidate-b",
                                    "nmu": "Mais velha",
                                    "dt": "01/01/1960",
                                    "vap": "30",
                                    "seq": "3",
                                    "dvt": "Válido",
                                },
                                {
                                    "n": "30",
                                    "sqcand": "candidate-c",
                                    "nmu": "Mais nova",
                                    "dt": "01/01/1970",
                                    "vap": "30",
                                    "seq": "2",
                                    "dvt": "Válido",
                                },
                            ],
                        }
                    ]
                }
            ],
        }

        candidates = main.extract_candidates(cargo, valid_votes=100)

        self.assertEqual(
            [candidate["name"] for candidate in candidates if candidate["inSeat"]],
            ["Líder", "Mais velha"],
        )

    def test_marks_proportional_vacancies_by_party_and_vote_threshold(self) -> None:
        cargo = {
            "cd": "6",
            "nv": "3",
            "qe": "1000",
            "agr": [
                {
                    "vag": "2",
                    "par": [
                        {
                            "sg": "ABC",
                            "cand": [
                                {
                                    "n": "10",
                                    "sqcand": "a",
                                    "nmu": "Candidato A",
                                    "vap": "300",
                                    "pvap": "30,00",
                                    "seq": "1",
                                    "dvt": "Válido",
                                },
                                {
                                    "n": "11",
                                    "sqcand": "b",
                                    "nmu": "Candidato B",
                                    "vap": "200",
                                    "pvap": "20,00",
                                    "seq": "2",
                                    "dvt": "Válido",
                                },
                                {
                                    "n": "12",
                                    "sqcand": "c",
                                    "nmu": "Candidato C",
                                    "vap": "99",
                                    "pvap": "9,90",
                                    "seq": "3",
                                    "dvt": "Válido",
                                },
                            ],
                        }
                    ],
                },
                {
                    "vag": "1",
                    "par": [
                        {
                            "sg": "XYZ",
                            "cand": [
                                {
                                    "n": "20",
                                    "sqcand": "d",
                                    "nmu": "Candidato D",
                                    "vap": "150",
                                    "pvap": "15,00",
                                    "seq": "4",
                                    "dvt": "Válido",
                                }
                            ],
                        }
                    ],
                },
            ],
        }

        candidates = main.extract_candidates(cargo)

        self.assertEqual(
            [candidate["name"] for candidate in candidates if candidate["inSeat"]],
            ["Candidato A", "Candidato B", "Candidato D"],
        )

    def test_official_elected_status_overrides_projection(self) -> None:
        cargo = {
            "cd": "3",
            "nv": "1",
            "agr": [
                {
                    "par": [
                        {
                            "sg": "ABC",
                            "cand": [
                                {
                                    "n": "10",
                                    "sqcand": "projected",
                                    "nmu": "Líder da apuração",
                                    "vap": "60",
                                    "pvap": "60,00",
                                    "seq": "1",
                                    "dvt": "Válido",
                                    "e": "n",
                                },
                                {
                                    "n": "20",
                                    "sqcand": "elected",
                                    "nmu": "Eleito oficialmente",
                                    "vap": "40",
                                    "pvap": "40,00",
                                    "seq": "2",
                                    "dvt": "Válido",
                                    "e": "s",
                                },
                            ],
                        }
                    ]
                }
            ],
        }

        candidates = main.extract_candidates(cargo, valid_votes=100)

        elected = next(candidate for candidate in candidates if candidate["name"] == "Eleito oficialmente")
        projected = next(candidate for candidate in candidates if candidate["name"] == "Líder da apuração")
        self.assertTrue(elected["officiallyElected"])
        self.assertEqual(elected["highlightStatus"], "elected")
        self.assertTrue(elected["inSeat"])
        self.assertFalse(projected["officiallyElected"])
        self.assertEqual(projected["highlightStatus"], "majority")

    def test_builds_city_result_response_from_official_data(self) -> None:
        election = make_election(cargos=[{"code": "3", "name": "Governador"}])
        locations = [
            {
                "cd": "sp",
                "ds": "SÃO PAULO",
                "mu": [{"cd": "71072", "nm": "SÃO PAULO"}],
            }
        ]
        payload = {
            "carg": [
                {
                    "cd": "3",
                    "agr": [
                        {
                            "com": "Partido",
                            "par": [
                                {
                                    "sg": "ABC",
                                    "cand": [
                                        {
                                            "n": "10",
                                            "sqcand": "123456789012",
                                            "nmu": "Candidata",
                                            "vap": "80",
                                            "pvap": "80,00",
                                            "seq": "1",
                                        },
                                        {
                                            "n": "20",
                                            "sqcand": "candidate-b",
                                            "nmu": "Candidato B",
                                            "vap": "20",
                                            "pvap": "20,00",
                                            "seq": "2",
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
            "s": {"st": "3", "ts": "10", "pst": "30,00"},
            "v": {"vvc": "100", "tv": "100", "vb": "7", "tvn": "3"},
            "dg": "04/10/2026",
            "hg": "18:00:00",
        }
        qualification_payload = {
            "carg": [
                {
                    "cd": "3",
                    "agr": [
                        {
                            "com": "Partido",
                            "par": [
                                {
                                    "sg": "ABC",
                                    "cand": [
                                        {
                                            "n": "10",
                                            "sqcand": "123456789012",
                                            "nmu": "Candidata",
                                            "vap": "40",
                                            "pvap": "40,00",
                                            "seq": "1",
                                        },
                                        {
                                            "n": "20",
                                            "sqcand": "candidate-b",
                                            "nmu": "Candidato B",
                                            "vap": "35",
                                            "pvap": "35,00",
                                            "seq": "2",
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
            "v": {"vvc": "100", "tv": "100"},
        }

        with (
            patch.object(main, "get_locations", return_value=locations),
            patch.object(
                main,
                "fetch_tse_payload",
                side_effect=[payload, qualification_payload],
            ) as fetch,
        ):
            result = main.make_result_response(election, "3", "sp", "71072")

        self.assertEqual(result["location"], "SÃO PAULO / SÃO PAULO")
        self.assertEqual(result["cargo"], "Governador")
        self.assertEqual(result["candidates"][0]["votes"], 80)
        self.assertEqual(
            result["candidates"][0]["photoUrl"],
            "https://resultados.tse.jus.br/oficial/ele2026/6259/fotos/sp/123456789012.jpeg",
        )
        self.assertIsNone(result["candidates"][1]["photoUrl"])
        self.assertEqual(result["votes"]["blank"], 7)
        self.assertEqual(result["votes"]["null"], 3)
        self.assertEqual(
            [candidate["highlightStatus"] for candidate in result["candidates"]],
            ["second_round", "second_round"],
        )
        self.assertIn("independentemente da cidade", result["highlightBasis"])
        self.assertEqual(
            fetch.call_args_list,
            [
                call(main.result_url(election, "3", "sp", "71072")),
                call(main.result_url(election, "3", "sp")),
            ],
        )

    def test_rejects_municipality_from_another_state(self) -> None:
        election = make_election(cargos=[{"code": "3", "name": "Governador"}])
        locations = [{"cd": "sp", "ds": "SÃO PAULO", "mu": []}]

        with patch.object(main, "get_locations", return_value=locations):
            with self.assertRaisesRegex(main.TSEDataError, "não pertence"):
                main.make_result_response(election, "3", "sp", "71072")

    def test_allows_brazil_level_presidential_results(self) -> None:
        election = make_election(
            election_id="6257",
            election_type="8",
            cargos=[{"code": "1", "name": "Presidente"}],
        )
        payload = {
            "carg": [
                {
                    "cd": "1",
                    "agr": [
                        {
                            "par": [
                                {
                                    "sg": "ABC",
                                    "cand": [
                                        {
                                            "n": "10",
                                            "sqcand": "123456789012",
                                            "nmu": "Candidata",
                                            "vap": "0",
                                            "pvap": "0,00",
                                            "seq": "1",
                                        }
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ],
            "s": {"st": "0", "ts": "100", "pst": "0,00"},
            "v": {"vvc": "0", "tv": "0", "vb": "0", "tvn": "0"},
        }

        with (
            patch.object(main, "get_locations", return_value=[]),
            patch.object(main, "fetch_tse_payload", return_value=payload) as fetch,
        ):
            result = main.make_result_response(election, "1", "br")

        self.assertEqual(result["location"], "Brasil")
        self.assertEqual(
            result["candidates"][0]["photoUrl"],
            "https://resultados.tse.jus.br/oficial/ele2026/6257/fotos/br/123456789012.jpeg",
        )
        fetch.assert_called_once_with(main.result_url(election, "1", "br"))


if __name__ == "__main__":
    unittest.main()
