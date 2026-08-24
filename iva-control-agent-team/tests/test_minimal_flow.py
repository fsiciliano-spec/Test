"""Test minimo end-to-end del sistema di controllo IVA.

Eseguibile con la sola libreria standard:

    python -m unittest discover -s tests -v

Verifica: caricamento configurazione agenti, creazione cartelle di output,
generazione di ``fonti_utilizzate.json`` e ``report_controllo_iva.md``,
gestione del caso "fonti mancanti" con esito ``DA SOSPENDERE`` e presenza
dell'agente ``report-writer-iva``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE / "src"))

import run_control  # noqa: E402
from normalize import (  # noqa: E402
    CARTELLE_ATTESE,
    ESITO_SOSPENDERE,
    GRAVITA_BLOCCANTE,
    normalizza_numero_documento,
    parse_data,
    parse_importo,
)

CONFIG = RADICE / "config" / "claude-code-agent-teams.json"
DEMO = RADICE / "examples" / "cliente_demo" / "IVA" / "2026" / "01" / "ALFA_SRL"

REGISTRO_VENDITE = """Data;Numero;Cliente;Partita IVA;Imponibile;Aliquota;Imposta;Sezionale
10/01/2026;1/2026;BETA S.P.A.;09876543210;1.000,00;22;220,00;1
"""

REGISTRO_ACQUISTI = """Data;Numero;Fornitore;Partita IVA;Imponibile;Aliquota;Imposta;Descrizione
12/01/2026;77;UFFICIO FORNITURE S.P.A.;33344455566;500,00;22;110,00;Cancelleria
"""

BOZZA = """Voce;Importo
IVA vendite;220,00
IVA acquisti detraibile;110,00
Credito periodo precedente;0,00
IVA da versare;110,00
"""


def crea_cartella_minimale(radice: Path, con_fonti_essenziali: bool = True) -> Path:
    """Crea una cartella di lavoro fittizia ``IVA/2026/01/CLIENTE_TEST``."""
    cartella = radice / "IVA" / "2026" / "01" / "CLIENTE_TEST"
    for nome in CARTELLE_ATTESE:
        (cartella / nome).mkdir(parents=True, exist_ok=True)
    if con_fonti_essenziali:
        (cartella / "02_registri_iva" / "registro_vendite_2026_01.csv").write_text(
            REGISTRO_VENDITE, encoding="utf-8")
        (cartella / "02_registri_iva" / "registro_acquisti_2026_01.csv").write_text(
            REGISTRO_ACQUISTI, encoding="utf-8")
        (cartella / "05_bozza_liquidazione" / "bozza.csv").write_text(BOZZA, encoding="utf-8")
    return cartella


class TestConfigurazioneAgenti(unittest.TestCase):
    """Caricamento e integrita' della configurazione del team."""

    def test_configurazione_caricabile(self):
        config = run_control.carica_config_agenti(CONFIG)
        self.assertIn("agents", config)
        self.assertEqual(len(config["agents"]), 12)

    def test_presenza_agente_report_writer(self):
        config = run_control.carica_config_agenti(CONFIG)
        self.assertIn("report-writer-iva", run_control.elenco_agenti(config))
        agente = config["agents"]["report-writer-iva"]
        self.assertTrue(agente["description"].strip())
        self.assertTrue(agente["prompt"].strip())

    def test_ogni_agente_ha_file_di_definizione(self):
        config = run_control.carica_config_agenti(CONFIG)
        for nome, definizione in config["agents"].items():
            percorso = RADICE / definizione["definition_file"]
            self.assertTrue(percorso.is_file(), f"definizione mancante per {nome}: {percorso}")

    def test_pipeline_allineata_agli_agenti(self):
        config = run_control.carica_config_agenti(CONFIG)
        self.assertEqual(sorted(config["pipeline"]), sorted(config["agents"]))


class TestNormalizzazione(unittest.TestCase):
    """Le normalizzazioni di base non devono inventare dati."""

    def test_importi_formato_italiano(self):
        self.assertEqual(parse_importo("1.234,56"), 1234.56)
        self.assertEqual(parse_importo("(1.234,56)"), -1234.56)
        self.assertEqual(parse_importo("1234.56"), 1234.56)
        self.assertIsNone(parse_importo("non un numero"))
        self.assertIsNone(parse_importo(""))

    def test_date(self):
        self.assertEqual(parse_data("31/01/2026"), "2026-01-31")
        self.assertEqual(parse_data("2026-01-31"), "2026-01-31")
        self.assertIsNone(parse_data("data illeggibile"))

    def test_numero_documento_tollerante(self):
        self.assertEqual(normalizza_numero_documento("0001/A"),
                         normalizza_numero_documento("1 - A"))
        self.assertEqual(normalizza_numero_documento("FPA 12/2026"),
                         normalizza_numero_documento("12/2026"))


class TestFlussoCompleto(unittest.TestCase):
    """Esecuzione end-to-end su una cartella con le fonti essenziali."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.radice = Path(self._tmp.name)
        self.cartella = crea_cartella_minimale(self.radice)
        self.output = self.cartella / "99_output_controllo"
        self.config = run_control.carica_config_agenti(CONFIG)

    def tearDown(self):
        self._tmp.cleanup()

    def test_creazione_cartella_output_e_file(self):
        contesto = run_control.esegui_controllo(self.cartella, self.output, self.config)
        self.assertTrue(self.output.is_dir(), "cartella di output non creata")
        for atteso in ("fonti_utilizzate.json", "report_controllo_iva.md"):
            self.assertTrue((self.output / atteso).is_file(), f"{atteso} non generato")
        self.assertEqual(contesto["cliente"], "CLIENTE_TEST")
        self.assertEqual(contesto["periodo"], "01")

    def test_audit_trail_traccia_le_fonti(self):
        run_control.esegui_controllo(self.cartella, self.output, self.config)
        audit = json.loads((self.output / "fonti_utilizzate.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["controllo"]["cliente"], "CLIENTE_TEST")
        self.assertEqual(len(audit["fonti"]), 3)
        file_tracciati = {voce["file"] for voce in audit["fonti"]}
        self.assertIn("05_bozza_liquidazione/bozza.csv", file_tracciati)
        for voce in audit["fonti"]:
            self.assertTrue(voce["sha256"], "hash della fonte non registrato")
            self.assertIn(voce["esito_lettura"], {"letto", "letto_parzialmente", "non_letto"})

    def test_report_ha_struttura_obbligatoria(self):
        contesto = run_control.esegui_controllo(self.cartella, self.output, self.config)
        report = (self.output / "report_controllo_iva.md").read_text(encoding="utf-8")
        for sezione in ("## 1. Esito sintetico", "## 2. Fonti analizzate",
                        "## 3. Quadratura liquidazione", "## 4. Anomalie principali",
                        "## 5. Altri rilievi informativi",
                        "## 6. Documenti o chiarimenti da richiedere",
                        "## 7. Conclusione operativa"):
            self.assertIn(sezione, report, f"sezione mancante nel report: {sezione}")
        self.assertIn(contesto["esito"], report)
        self.assertNotIn("{{", report, "segnaposto del template non sostituiti")

    def test_liquidazione_ricalcolata_dai_registri(self):
        contesto = run_control.esegui_controllo(self.cartella, self.output, self.config)
        liquidazione = contesto["liquidazione"]
        self.assertEqual(liquidazione.iva_vendite, 220.00)
        self.assertEqual(liquidazione.iva_detraibile, 110.00)
        self.assertEqual(liquidazione.saldo_periodo, 110.00)
        differenze = [v["differenza"] for v in contesto["quadratura"] if v["differenza"] is not None]
        self.assertTrue(all(abs(d) < 0.01 for d in differenze),
                        f"differenze inattese rispetto alla bozza: {contesto['quadratura']}")


class TestFontiMancanti(unittest.TestCase):
    """In assenza di fonti essenziali l'esito non puo' essere forzato a OK."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.radice = Path(self._tmp.name)
        self.cartella = crea_cartella_minimale(self.radice, con_fonti_essenziali=False)
        self.output = self.cartella / "99_output_controllo"
        self.config = run_control.carica_config_agenti(CONFIG)

    def tearDown(self):
        self._tmp.cleanup()

    def test_esito_da_sospendere(self):
        contesto = run_control.esegui_controllo(self.cartella, self.output, self.config)
        self.assertEqual(contesto["esito"], ESITO_SOSPENDERE)
        self.assertFalse(contesto["controllo_completo"])
        self.assertEqual(sorted(contesto["fonti_mancanti"]),
                         ["02_registri_iva", "05_bozza_liquidazione"])

    def test_anomalie_bloccanti_per_fonti_essenziali(self):
        contesto = run_control.esegui_controllo(self.cartella, self.output, self.config)
        bloccanti = [a for a in contesto["anomalie"] if a.gravita == GRAVITA_BLOCCANTE]
        self.assertTrue(bloccanti, "nessun rilievo bloccante per le fonti essenziali mancanti")
        self.assertTrue(any(a.codice.startswith("ORC-001") for a in bloccanti))

    def test_richiesta_documenti_generata(self):
        run_control.esegui_controllo(self.cartella, self.output, self.config)
        richiesta = self.output / "richiesta_documenti_cliente.md"
        self.assertTrue(richiesta.is_file(), "richiesta documenti non generata")
        testo = richiesta.read_text(encoding="utf-8")
        self.assertIn("02_registri_iva", testo)
        self.assertIn(ESITO_SOSPENDERE, testo)

    def test_report_dichiara_il_limite(self):
        run_control.esegui_controllo(self.cartella, self.output, self.config)
        report = (self.output / "report_controllo_iva.md").read_text(encoding="utf-8")
        self.assertIn(ESITO_SOSPENDERE, report)
        self.assertIn("02_registri_iva", report)


class TestEsempioDemo(unittest.TestCase):
    """Il cliente dimostrativo deve restare eseguibile e riproducibile."""

    @unittest.skipUnless(DEMO.is_dir(), "cartella di esempio non presente")
    def test_demo_produce_rilievi_attesi(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "99_output_controllo"
            config = run_control.carica_config_agenti(CONFIG)
            contesto = run_control.esegui_controllo(DEMO, output, config)
        codici = {a.codice.rsplit(".", 1)[0] for a in contesto["anomalie"]}
        # Fattura attiva presente fra gli XML e non registrata.
        self.assertIn("XML-REG-001", codici)
        # IVA su autovettura detratta integralmente.
        self.assertIn("DET-001", codici)
        self.assertEqual(contesto["esito"], ESITO_SOSPENDERE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
