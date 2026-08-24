#!/usr/bin/env python3
"""Comando principale del controllo IVA.

    python src/run_control.py --input IVA/2026/01/CLIENTE_X \
                              --output IVA/2026/01/CLIENTE_X/99_output_controllo

Esegue la pipeline degli agenti sulle fonti disponibili, produce dataset,
anomalie, audit trail e report, e dichiara sempre se il controllo e' completo
o limitato dalle fonti mancanti.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path

_QUI = Path(__file__).resolve().parent
if str(_QUI) not in sys.path:  # esecuzione diretta dello script
    sys.path.insert(0, str(_QUI))

import controlli
import export_outputs
import liquidazione_checker
import match_xml_registri
import parse_excel
import parse_pdf
import parse_xml
from normalize import (
    Anomalia,
    CARTELLA_OUTPUT,
    CARTELLE_ATTESE,
    ESITO_OK,
    ESITO_SOSPENDERE,
    ESITO_VERIFICA,
    FONTI_ESSENZIALI,
    GRAVITA_BLOCCANTE,
    GRAVITA_INFO,
    GRAVITA_VERIFICA,
    IMPATTO_NON_DETERMINABILE,
    Riga,
    euro,
    mesi_del_periodo,
    normalizza_piva,
    parse_importo,
    peggior_esito,
    somma,
)

RADICE_PROGETTO = _QUI.parent
CONFIG_DEFAULT = RADICE_PROGETTO / "config" / "claude-code-agent-teams.json"
TEMPLATE_REPORT = RADICE_PROGETTO / "templates" / "template_report_controllo_iva.md"
VERSIONE = "1.0.0"

log = logging.getLogger("iva-control")

#: Mappatura cartella -> (origine dataset, etichetta leggibile).
ORIGINI_PER_CARTELLA = {
    "02_registri_iva": ("registro", "Registri IVA"),
    "03_corrispettivi": ("corrispettivi", "Corrispettivi"),
    "04_prima_nota": ("prima_nota", "Prima nota"),
    "05_bozza_liquidazione": ("bozza_liquidazione", "Bozza di liquidazione"),
    "06_crediti_precedenti_f24_lipe": ("crediti_f24_lipe", "F24 / LIPE / crediti precedenti"),
}


# ---------------------------------------------------------------------------
# Configurazione agenti
# ---------------------------------------------------------------------------

def carica_config_agenti(percorso: Path | str = CONFIG_DEFAULT) -> dict:
    """Carica e valida la configurazione del team di agenti."""
    percorso = Path(percorso)
    with percorso.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if "agents" not in config or not isinstance(config["agents"], dict):
        raise ValueError(f"configurazione priva della sezione 'agents': {percorso}")
    for nome, definizione in config["agents"].items():
        for campo in ("description", "prompt"):
            if not definizione.get(campo):
                raise ValueError(f"agente {nome}: campo '{campo}' mancante o vuoto")
    return config


def elenco_agenti(config: dict) -> list[str]:
    return list(config.get("agents", {}).keys())


# ---------------------------------------------------------------------------
# Scansione e classificazione delle fonti
# ---------------------------------------------------------------------------

def _hash_file(percorso: Path) -> str:
    digest = hashlib.sha256()
    with percorso.open("rb") as handle:
        for blocco in iter(lambda: handle.read(1 << 16), b""):
            digest.update(blocco)
    return digest.hexdigest()


def classifica_registro(percorso: Path) -> str:
    """Deduce il tipo di registro dal nome del file."""
    nome = percorso.name.lower()
    if "vendit" in nome or "emesse" in nome or "attive" in nome:
        return "registro_vendite"
    if "acquist" in nome or "ricevute" in nome or "passive" in nome:
        return "registro_acquisti"
    if "corrispettiv" in nome:
        return "corrispettivi"
    return "registro_non_classificato"


def scansiona(cartella_input: Path) -> dict:
    """Costruisce la checklist documentale della cartella di lavoro."""
    checklist = []
    for nome in CARTELLE_ATTESE:
        percorso = cartella_input / nome
        if not percorso.is_dir():
            checklist.append({"nome": nome, "stato": "assente", "file": 0, "elenco": []})
            continue
        file_trovati = [p for p in sorted(percorso.rglob("*")) if p.is_file() and not p.name.startswith(".")]
        checklist.append({
            "nome": nome,
            "stato": "presente" if file_trovati else "vuota",
            "file": len(file_trovati),
            "elenco": [str(p.relative_to(cartella_input)) for p in file_trovati],
        })
    return {"cartelle": checklist}


def deduci_periodo(cartella_input: Path) -> tuple[str, str, str]:
    """Ricava ``(cliente, anno, periodo)`` dalla struttura ``IVA/{anno}/{periodo}/{cliente}``."""
    parti = cartella_input.resolve().parts
    cliente = parti[-1] if parti else "n.d."
    periodo = parti[-2] if len(parti) >= 2 else ""
    anno = parti[-3] if len(parti) >= 3 else ""
    if not (anno.isdigit() and len(anno) == 4):
        anno = ""
    return cliente, anno, periodo


def deduci_piva_cliente(cartella_input: Path, cartella_xml: Path) -> tuple[str, str]:
    """Determina la P.IVA del cliente. Restituisce ``(piva, fonte)``."""
    profilo = cartella_input / "profilo_cliente.json"
    if profilo.is_file():
        try:
            dati = json.loads(profilo.read_text(encoding="utf-8"))
            piva = str(dati.get("partita_iva", "")).strip()
            if piva:
                return piva, "profilo_cliente.json"
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("profilo_cliente.json non leggibile: %s", exc)

    # Fallback: la P.IVA che compare piu' spesso come controparte fissa negli XML.
    if not cartella_xml.is_dir():
        return "", ""
    conteggio: Counter[str] = Counter()
    for percorso in sorted(cartella_xml.rglob("*")):
        if percorso.suffix.lower() not in {".xml", ".p7m"} or not percorso.is_file():
            continue
        try:
            contenuto = parse_xml.estrai_xml(percorso)
            import xml.etree.ElementTree as ET

            radice = parse_xml._senza_namespace(ET.fromstring(contenuto))
        except Exception:
            continue
        for percorso_nodo in ("FatturaElettronicaHeader/CedentePrestatore/DatiAnagrafici/IdFiscaleIVA/IdCodice",
                              "FatturaElettronicaHeader/CessionarioCommittente/DatiAnagrafici/IdFiscaleIVA/IdCodice"):
            nodo = radice.find(percorso_nodo)
            if nodo is not None and nodo.text:
                conteggio[normalizza_piva(nodo.text)] += 1
    if not conteggio:
        return "", ""
    piva, occorrenze = conteggio.most_common(1)[0]
    totale_file = sum(conteggio.values())
    if occorrenze >= max(2, totale_file // 3):
        return piva, "dedotta dalla ricorrenza negli XML"
    return "", ""


# ---------------------------------------------------------------------------
# Lettura delle fonti
# ---------------------------------------------------------------------------

def leggi_fonti(cartella_input: Path, piva_cliente: str) -> tuple[dict[str, list[Riga]], list[dict], dict, list[Anomalia]]:
    """Legge tutte le fonti disponibili.

    Restituisce ``(dataset, log_fonti, bozza, anomalie_di_lettura)``.
    """
    dataset: dict[str, list[Riga]] = defaultdict(list)
    log_fonti: list[dict] = []
    anomalie: list[Anomalia] = []
    bozza: dict[str, dict] = {}
    codice = controlli.Contatore("SRC")

    # --- XML fatture -------------------------------------------------------
    cartella_xml = cartella_input / "01_xml_fatture"
    if cartella_xml.is_dir():
        righe, voci = parse_xml.leggi_cartella(cartella_xml, piva_cliente)
        for riga in righe:
            dataset[riga.origine].append(riga)
        for voce in voci:
            percorso = Path(voce["file"])
            log_fonti.append({
                "file": str(percorso.relative_to(cartella_input)),
                "cartella": "01_xml_fatture",
                "estensione": percorso.suffix.lower(),
                "dimensione_byte": percorso.stat().st_size,
                "sha256": _hash_file(percorso),
                "tipo_riconosciuto": "fattura_elettronica",
                "parser": "parse_xml.leggi_fattura",
                "righe_estratte": voce["righe"],
                "esito_lettura": voce["esito"],
                "note": voce["note"],
            })
            if voce["esito"] == "non_letto":
                anomalie.append(Anomalia(
                    codice=codice("001"),
                    gravita=GRAVITA_VERIFICA,
                    fonte=percorso.name,
                    descrizione=f"File XML non letto: {voce['note']}.",
                    impatto_iva=IMPATTO_NON_DETERMINABILE,
                    azione="richiesta documento",
                    agente="intake-normalizzazione-iva",
                ))
            elif voce["note"]:
                anomalie.append(Anomalia(
                    codice=codice("006"),
                    gravita=GRAVITA_INFO,
                    fonte=percorso.name,
                    descrizione=f"Lettura XML con avvisi: {voce['note']}.",
                    impatto_iva=IMPATTO_NON_DETERMINABILE,
                    azione="verifica manuale",
                    agente="intake-normalizzazione-iva",
                ))

    # --- Altre cartelle ----------------------------------------------------
    hash_visti: dict[str, str] = {}
    for cartella, (famiglia, etichetta) in ORIGINI_PER_CARTELLA.items():
        percorso_cartella = cartella_input / cartella
        if not percorso_cartella.is_dir():
            continue
        for percorso in sorted(percorso_cartella.rglob("*")):
            if not percorso.is_file() or percorso.name.startswith("."):
                continue
            relativo = str(percorso.relative_to(cartella_input))
            impronta = _hash_file(percorso)
            voce_log = {
                "file": relativo,
                "cartella": cartella,
                "estensione": percorso.suffix.lower(),
                "dimensione_byte": percorso.stat().st_size,
                "sha256": impronta,
                "tipo_riconosciuto": famiglia,
                "parser": "",
                "righe_estratte": 0,
                "esito_lettura": "non_letto",
                "note": "",
            }

            if impronta in hash_visti:
                voce_log["note"] = f"duplicato di {hash_visti[impronta]}"
                anomalie.append(Anomalia(
                    codice=codice("005"),
                    gravita=GRAVITA_INFO,
                    fonte=relativo,
                    descrizione=f"File identico (stesso hash) a {hash_visti[impronta]}: letto una sola volta.",
                    impatto_iva=IMPATTO_NON_DETERMINABILE,
                    azione="controllo gestionale",
                    agente="intake-normalizzazione-iva",
                ))
                log_fonti.append(voce_log)
                continue
            hash_visti[impronta] = relativo

            estensione = percorso.suffix.lower()
            try:
                if cartella == "05_bozza_liquidazione" and estensione == ".pdf":
                    voci_bozza, log_pdf = parse_pdf.leggi_bozza_liquidazione(percorso)
                    bozza.update(voci_bozza)
                    voce_log.update(parser="parse_pdf.leggi_bozza_liquidazione",
                                    righe_estratte=log_pdf["righe"],
                                    esito_lettura=log_pdf["esito"], note=log_pdf["note"])
                elif cartella == "05_bozza_liquidazione" and estensione in parse_excel.ESTENSIONI_TABELLARI:
                    voci_bozza, log_tab = _leggi_bozza_tabellare(percorso)
                    bozza.update(voci_bozza)
                    voce_log.update(parser="run_control._leggi_bozza_tabellare",
                                    righe_estratte=len(voci_bozza),
                                    esito_lettura=log_tab["esito"], note=log_tab["note"])
                elif estensione in parse_excel.ESTENSIONI_TABELLARI:
                    origine = classifica_registro(percorso) if cartella == "02_registri_iva" else famiglia
                    righe, log_tab = parse_excel.leggi_tabella(percorso, origine)
                    dataset[origine].extend(righe)
                    voce_log.update(parser="parse_excel.leggi_tabella", tipo_riconosciuto=origine,
                                    righe_estratte=len(righe), esito_lettura=log_tab["esito"],
                                    note=log_tab["note"])
                elif estensione == ".pdf":
                    origine = classifica_registro(percorso) if cartella == "02_registri_iva" else famiglia
                    righe, log_pdf = parse_pdf.leggi_registro_pdf(percorso, origine)
                    dataset[origine].extend(righe)
                    voce_log.update(parser="parse_pdf.leggi_registro_pdf", tipo_riconosciuto=origine,
                                    righe_estratte=len(righe), esito_lettura=log_pdf["esito"],
                                    note=log_pdf["note"])
                else:
                    voce_log["note"] = f"estensione {estensione} non gestita dai parser disponibili"
            except (parse_excel.ErroreLettura, parse_pdf.ErroreLettura, OSError) as exc:
                voce_log.update(esito_lettura="non_letto", note=str(exc))

            if voce_log["esito_lettura"] == "non_letto":
                essenziale = cartella in FONTI_ESSENZIALI
                anomalie.append(Anomalia(
                    codice=codice("002" if "scansione" in voce_log["note"] else "001"),
                    gravita=GRAVITA_BLOCCANTE if essenziale else GRAVITA_VERIFICA,
                    fonte=relativo,
                    descrizione=f"{etichetta}: file non letto — {voce_log['note'] or 'parser non disponibile'}.",
                    impatto_iva=IMPATTO_NON_DETERMINABILE,
                    azione="richiesta documento",
                    agente="intake-normalizzazione-iva",
                ))
            elif voce_log["esito_lettura"] == "letto_parzialmente":
                anomalie.append(Anomalia(
                    codice=codice("003"),
                    gravita=GRAVITA_VERIFICA,
                    fonte=relativo,
                    descrizione=f"{etichetta}: lettura parziale — {voce_log['note']}.",
                    impatto_iva=IMPATTO_NON_DETERMINABILE,
                    azione="verifica manuale",
                    agente="intake-normalizzazione-iva",
                ))
            log_fonti.append(voce_log)

    return dict(dataset), log_fonti, bozza, anomalie


def _leggi_bozza_tabellare(percorso: Path) -> tuple[dict[str, dict], dict]:
    """Legge una bozza di liquidazione in formato ``voce;importo``."""
    import csv

    voci: dict[str, dict] = {}
    log = {"esito": "letto", "note": ""}
    coppie: list[tuple[str, str]] = []

    if percorso.suffix.lower() in {".csv", ".tsv", ".txt"}:
        with percorso.open("r", encoding="utf-8-sig", newline="") as handle:
            contenuto = handle.read()
        delimitatore = ";" if contenuto.count(";") >= contenuto.count(",") else ","
        for riga in csv.reader(contenuto.splitlines(), delimiter=delimitatore):
            if len(riga) >= 2:
                coppie.append((riga[0], riga[-1]))
    else:
        if parse_excel.load_workbook is None:
            log.update(esito="non_letto", note="openpyxl non disponibile")
            return voci, log
        workbook = parse_excel.load_workbook(percorso, data_only=True, read_only=True)
        try:
            for ws in workbook.worksheets:
                for riga in ws.iter_rows(values_only=True):
                    valori = [c for c in riga if c is not None]
                    if len(valori) >= 2:
                        coppie.append((str(valori[0]), str(valori[-1])))
        finally:
            workbook.close()

    for numero, (etichetta, valore) in enumerate(coppie, start=1):
        testo = str(etichetta).strip().lower()
        importo = parse_importo(valore)
        if importo is None:
            continue
        for campo, varianti in parse_pdf.ETICHETTE_LIQUIDAZIONE.items():
            if campo in voci:
                continue
            if any(v in testo for v in varianti):
                if "credito" in testo and campo == "saldo" and importo > 0:
                    importo = -importo
                voci[campo] = {"importo": importo, "pagina": None, "riga": f"{etichetta}: {valore}",
                               "riga_foglio": numero}
                break

    if not voci:
        log.update(esito="letto_parzialmente", note="nessuna voce di liquidazione riconosciuta")
    else:
        mancanti = [c for c in ("iva_vendite", "iva_acquisti", "saldo") if c not in voci]
        if mancanti:
            log.update(esito="letto_parzialmente", note="voci non individuate: " + ", ".join(mancanti))
    return voci, log


def estrai_f24(dataset: dict[str, list[Riga]]) -> list[dict]:
    """Estrae le voci F24 dai documenti della cartella 06."""
    import re

    voci: list[dict] = []
    for riga in dataset.get("crediti_f24_lipe", []):
        testo = f"{riga.descrizione} {riga.controparte} {riga.numero}"
        tributo = re.search(r"\b(6\d{3})\b", testo)
        importo = riga.totale_documento if riga.totale_documento is not None else riga.imposta
        if tributo or "f24" in testo.lower():
            voci.append({
                "codice_tributo": tributo.group(1) if tributo else "",
                "importo": importo,
                "data": riga.data,
                "fonte": riga.fonte.testo(),
            })
    return voci


# ---------------------------------------------------------------------------
# Pipeline di controllo
# ---------------------------------------------------------------------------

def esegui_controllo(
    cartella_input: Path,
    cartella_output: Path,
    config: dict,
    piva_cliente: str = "",
    trimestrale: bool | None = None,
) -> dict:
    """Esegue l'intera pipeline e scrive gli output. Restituisce il contesto del controllo."""
    cartella_output.mkdir(parents=True, exist_ok=True)
    cliente, anno, periodo = deduci_periodo(cartella_input)
    checklist = scansiona(cartella_input)
    anomalie: list[Anomalia] = []
    codice_orc = controlli.Contatore("ORC")

    # --- Fonti essenziali --------------------------------------------------
    stato_cartelle = {c["nome"]: c for c in checklist["cartelle"]}
    fonti_mancanti = [
        nome for nome in FONTI_ESSENZIALI
        if stato_cartelle.get(nome, {}).get("stato") != "presente"
    ]
    for nome in fonti_mancanti:
        anomalie.append(Anomalia(
            codice=codice_orc("001"),
            gravita=GRAVITA_BLOCCANTE,
            fonte=f"cartella di lavoro {cartella_input.name}/{nome}",
            descrizione=(
                f"Fonte essenziale assente o vuota: `{nome}`. "
                "Il controllo non e' completabile senza questa fonte."
            ),
            impatto_iva=IMPATTO_NON_DETERMINABILE,
            azione="richiesta documento",
            agente="orchestratore-iva",
        ))

    # --- Profilo del cliente ----------------------------------------------
    fonte_piva = "parametro --piva" if piva_cliente else ""
    if not piva_cliente:
        piva_cliente, fonte_piva = deduci_piva_cliente(cartella_input, cartella_input / "01_xml_fatture")
    if not piva_cliente and (cartella_input / "01_xml_fatture").is_dir():
        anomalie.append(Anomalia(
            codice=codice_orc("004"),
            gravita=GRAVITA_VERIFICA,
            fonte="cartella 01_xml_fatture",
            descrizione=(
                "P.IVA del cliente non determinabile dai documenti: le fatture XML non sono state "
                "distinte fra attive e passive. Fornire `profilo_cliente.json` o l'opzione `--piva`."
            ),
            impatto_iva=IMPATTO_NON_DETERMINABILE,
            azione="richiesta documento",
            agente="orchestratore-iva",
        ))

    mesi = mesi_del_periodo(anno, periodo)
    if trimestrale is None:
        trimestrale = len(mesi) == 3
    if not mesi:
        anomalie.append(Anomalia(
            codice=codice_orc("002"),
            gravita=GRAVITA_VERIFICA,
            fonte=f"percorso {cartella_input}",
            descrizione=(
                f"Periodicita' IVA non determinabile dal percorso (anno «{anno}», periodo «{periodo}»): "
                "i controlli di competenza sono stati eseguiti senza filtro di periodo."
            ),
            impatto_iva=IMPATTO_NON_DETERMINABILE,
            azione="verifica manuale",
            agente="orchestratore-iva",
        ))

    # --- Intake ------------------------------------------------------------
    dataset, log_fonti, bozza, anomalie_lettura = leggi_fonti(cartella_input, piva_cliente)
    anomalie.extend(anomalie_lettura)

    xml_attive = dataset.get("xml_attiva", [])
    xml_passive = dataset.get("xml_passiva", [])
    xml_indeterminate = dataset.get("xml_indeterminata", [])
    registro_vendite = dataset.get("registro_vendite", [])
    registro_acquisti = dataset.get("registro_acquisti", [])
    corrispettivi = dataset.get("corrispettivi", [])
    prima_nota = dataset.get("prima_nota", [])
    non_classificati = dataset.get("registro_non_classificato", [])

    if non_classificati:
        anomalie.append(Anomalia(
            codice=codice_orc("003"),
            gravita=GRAVITA_VERIFICA,
            fonte=non_classificati[0].fonte.testo(),
            descrizione=(
                f"{len(non_classificati)} righe provengono da registri il cui tipo (vendite/acquisti) "
                "non e' deducibile dal nome del file: non sono state incluse nei totali."
            ),
            impatto_iva=IMPATTO_NON_DETERMINABILE,
            azione="verifica manuale",
            agente="registri-iva",
        ))

    # --- Registri ----------------------------------------------------------
    anomalie += controlli.controlla_registri(registro_vendite, mesi, "Registro vendite",
                                             controlla_numerazione=True)
    anomalie += controlli.controlla_registri(registro_acquisti, mesi, "Registro acquisti")

    # --- Quadratura XML/registri ------------------------------------------
    tabella_matching: list[dict] = []
    statistiche_match: dict = {}
    contatore_match: dict[str, int] = {}  # codici progressivi unici sulle due direzioni
    if xml_attive and registro_vendite:
        tabella, rilievi = match_xml_registri.confronta(
            xml_attive, registro_vendite, "attive", contatore_match,
            righe_reverse=[r for r in xml_passive + xml_indeterminate if r.reverse_charge])
        tabella_matching += tabella
        anomalie += rilievi
    if xml_passive and registro_acquisti:
        tabella, rilievi = match_xml_registri.confronta(
            xml_passive, registro_acquisti, "passive", contatore_match)
        tabella_matching += tabella
        anomalie += rilievi
    if tabella_matching:
        statistiche_match = match_xml_registri.statistiche(tabella_matching)

    # --- Corrispettivi -----------------------------------------------------
    rilievi_corrispettivi, riepilogo_corrispettivi = controlli.controlla_corrispettivi(corrispettivi, mesi)
    anomalie += rilievi_corrispettivi

    # --- Detraibilita' -----------------------------------------------------
    anomalie += controlli.controlla_detraibilita(registro_acquisti + xml_passive)

    # --- Reverse charge / split payment / estero --------------------------
    rilievi_reverse, riepilogo_reverse = controlli.controlla_reverse_split_estero(
        xml_passive + xml_indeterminate, registro_vendite, registro_acquisti
    )
    anomalie += rilievi_reverse

    # --- Liquidazione ------------------------------------------------------
    credito_precedente = bozza.get("credito_precedente", {}).get("importo")
    debito_precedente = bozza.get("debito_precedente", {}).get("importo") or 0.0
    liquidazione = liquidazione_checker.ricalcola(
        registro_vendite, registro_acquisti, corrispettivi,
        # L'IVA delle operazioni in inversione contabile e' gia' compresa nei totali
        # dei registri quando la doppia annotazione e' presente: sommarla di nuovo
        # significherebbe contarla due volte. L'agente reverse-split-estero-iva ne
        # verifica la presenza, non ne alimenta i totali.
        reverse_debito=0.0,
        reverse_credito=0.0,
        credito_precedente=credito_precedente,
        debito_precedente=debito_precedente,
        acconto=bozza.get("acconto", {}).get("importo") or 0.0,
        trimestrale=bool(trimestrale),
        ultimo_periodo=bool(mesi) and mesi[-1].endswith("-12"),
        fonti={
            "bozza": "05_bozza_liquidazione",
            "iva_vendite": "registro vendite normalizzato",
            "iva_acquisti": "registro acquisti normalizzato",
            "iva_corrispettivi": "registro corrispettivi normalizzato",
        },
    )
    quadratura, rilievi_liquidazione, esito_numerico = liquidazione_checker.confronta_con_bozza(
        liquidazione, bozza
    )
    anomalie += rilievi_liquidazione

    # --- Prima nota / F24 --------------------------------------------------
    anomalie += controlli.controlla_prima_nota(
        prima_nota, estrai_f24(dataset), debito_precedente or None, bool(trimestrale)
    )

    # --- Quality review ----------------------------------------------------
    anomalie, correzioni = controlli.quality_review(anomalie)

    # --- Esito -------------------------------------------------------------
    esito = _determina_esito(anomalie, fonti_mancanti, esito_numerico, liquidazione)

    # --- Output ------------------------------------------------------------
    contesto = _componi_contesto(
        cliente=cliente, anno=anno, periodo=periodo, mesi=mesi, trimestrale=bool(trimestrale),
        piva=piva_cliente, fonte_piva=fonte_piva, checklist=checklist,
        fonti_mancanti=fonti_mancanti, dataset=dataset, bozza=bozza,
        liquidazione=liquidazione, quadratura=quadratura, anomalie=anomalie,
        correzioni=correzioni, esito=esito, statistiche_match=statistiche_match,
        riepilogo_corrispettivi=riepilogo_corrispettivi, riepilogo_reverse=riepilogo_reverse,
        cartella_input=cartella_input, cartella_output=cartella_output, log_fonti=log_fonti,
    )
    _scrivi_output(contesto, dataset, anomalie, tabella_matching, quadratura, cartella_output)
    return contesto


def _determina_esito(
    anomalie: list[Anomalia],
    fonti_mancanti: list[str],
    esito_numerico: str,
    liquidazione: liquidazione_checker.Liquidazione,
) -> str:
    """Applica la logica di classificazione dell'esito finale."""
    if fonti_mancanti or not liquidazione.completa:
        return ESITO_SOSPENDERE
    if any(a.gravita == GRAVITA_BLOCCANTE for a in anomalie):
        return ESITO_SOSPENDERE
    if any(a.gravita == GRAVITA_VERIFICA for a in anomalie):
        return peggior_esito(ESITO_VERIFICA, esito_numerico)
    return peggior_esito(ESITO_OK, esito_numerico)


def _componi_contesto(**dati) -> dict:
    """Costruisce il contesto testuale per il Report Writer."""
    cliente = dati["cliente"]
    anomalie: list[Anomalia] = dati["anomalie"]
    liquidazione: liquidazione_checker.Liquidazione = dati["liquidazione"]
    esito = dati["esito"]
    mesi = dati["mesi"]
    periodo_esteso = (
        f"{dati['periodo']} {dati['anno']}".strip()
        + (f" ({mesi[0]} — {mesi[-1]})" if mesi else "")
    )

    bloccanti = [a for a in anomalie if a.gravita == GRAVITA_BLOCCANTE]
    da_verificare = [a for a in anomalie if a.gravita == GRAVITA_VERIFICA]
    informative = [a for a in anomalie if a.gravita == GRAVITA_INFO]

    # Sezione 2 - fonti.
    righe_fonti = []
    etichette = {
        "01_xml_fatture": "XML fatture",
        "02_registri_iva": "Registri IVA",
        "03_corrispettivi": "Corrispettivi",
        "04_prima_nota": "Prima nota",
        "05_bozza_liquidazione": "Bozza liquidazione",
        "06_crediti_precedenti_f24_lipe": "F24 / LIPE / crediti precedenti",
    }
    conteggio_righe = {k: len(v) for k, v in dati["dataset"].items()}
    for cartella in dati["checklist"]["cartelle"]:
        nome = cartella["nome"]
        stato = cartella["stato"]
        marcatore = {"presente": "presente", "vuota": "vuota", "assente": "**assente**"}[stato]
        righe_fonti.append(f"- **{etichette.get(nome, nome)}**: {marcatore}, {cartella['file']} file")
    righe_fonti.append(
        "- Righe normalizzate: " + (", ".join(f"{k} {v}" for k, v in sorted(conteggio_righe.items()))
                                    or "nessuna")
    )
    if dati["piva"]:
        righe_fonti.append(f"- P.IVA del cliente utilizzata per la ripartizione attive/passive: "
                           f"{dati['piva']} ({dati['fonte_piva'] or 'fonte non indicata'})")
    if not export_outputs.excel_disponibile():
        righe_fonti.append("- *Nota tecnica: `openpyxl` non disponibile, gli allegati sono stati "
                           "prodotti in formato CSV.*")

    # Sezione 1 - sintesi.
    differenze = [v for v in dati["quadratura"] if v["differenza"] not in (None, 0.0)]
    sintesi = []
    if dati["fonti_mancanti"]:
        sintesi.append(
            "- Fonti essenziali mancanti: " + ", ".join(f"`{f}`" for f in dati["fonti_mancanti"])
            + ". Il controllo non e' completabile."
        )
    controlli_svolti = []
    if dati["statistiche_match"]:
        s = dati["statistiche_match"]
        controlli_svolti.append(
            f"quadratura XML/registri su {s['documenti_confrontati']} documenti "
            f"(copertura {s['copertura_perc']}%)"
        )
    if dati["dataset"].get("registro_vendite") or dati["dataset"].get("registro_acquisti"):
        controlli_svolti.append("controllo numerazione, date e quadratura di riga dei registri")
    if dati["riepilogo_corrispettivi"]["giorni_presenti"]:
        controlli_svolti.append(
            f"corrispettivi su {dati['riepilogo_corrispettivi']['giorni_presenti']} giorni registrati"
        )
    if dati["riepilogo_reverse"]["operazioni"]:
        controlli_svolti.append(
            f"verifica doppia annotazione su {dati['riepilogo_reverse']['operazioni']} operazioni "
            "in inversione contabile"
        )
    if dati["bozza"]:
        controlli_svolti.append("ricalcolo della liquidazione e confronto con la bozza")
    sintesi.append("- Controlli effettuati: " + ("; ".join(controlli_svolti) if controlli_svolti
                                                 else "nessuno, per assenza di fonti leggibili") + ".")
    if differenze:
        sintesi.append(
            "- Differenze rilevate: " + "; ".join(
                f"{v['voce']} {euro(v['differenza'])}" for v in differenze
            ) + "."
        )
    else:
        sintesi.append("- Nessuna differenza fra ricalcolo e bozza oltre la tolleranza di arrotondamento."
                       if dati["bozza"] else "- Ricalcolo non confrontabile: bozza di liquidazione non letta.")
    if not liquidazione.completa:
        sintesi.append("- Ricalcolo **parziale**: non determinabili "
                       + ", ".join(liquidazione.voci_non_ricalcolabili) + ".")
    sintesi.append(f"- Anomalie: {len(bloccanti)} bloccanti, {len(da_verificare)} da verificare, "
                   f"{len(informative)} informative.")
    sintesi.append(_conclusione_operativa(esito, bloccanti, dati["fonti_mancanti"]))

    # Sezione 6 - richieste.
    voci_richiesta: list[str] = []
    for nome in dati["fonti_mancanti"]:
        voci_richiesta.append(
            f"`{nome}`: fonte essenziale assente, necessaria per completare il controllo."
        )
    for anomalia in anomalie:
        if anomalia.azione == "richiesta documento":
            voci_richiesta.append(f"{anomalia.descrizione} *(rif. {anomalia.codice}, fonte: {anomalia.fonte})*")
    richieste = ("\n".join(f"{i}. {v}" for i, v in enumerate(voci_richiesta, start=1))
                 if voci_richiesta else "Nessun documento o chiarimento da richiedere.")

    nota_quadratura = ""
    if not liquidazione.completa:
        nota_quadratura = ("Le voci contrassegnate `n.d.` non sono state ricalcolate per assenza della "
                           "fonte corrispondente; il confronto con la bozza e' quindi parziale.")
    elif not dati["bozza"]:
        nota_quadratura = ("La bozza di liquidazione non e' stata letta: la colonna «Da bozza liquidazione» "
                           "resta vuota e il confronto non e' stato eseguito.")

    return {
        "cliente": cliente,
        "anno": dati["anno"],
        "periodo": dati["periodo"],
        "periodo_esteso": periodo_esteso or "periodo non determinato",
        "periodicita": "trimestrale" if dati["trimestrale"] else "mensile",
        "piva": dati["piva"],
        "esito": esito,
        "sintesi": "\n".join(sintesi),
        "fonti_testo": "\n".join(righe_fonti),
        "fonti_mancanti_testo": ", ".join(f"`{f}`" for f in dati["fonti_mancanti"]) or "nessuna",
        "fonti_mancanti": dati["fonti_mancanti"],
        "quadratura": dati["quadratura"],
        "nota_quadratura": nota_quadratura,
        "anomalie": anomalie,
        "correzioni": dati["correzioni"],
        "richieste": richieste,
        "voci_richiesta": voci_richiesta,
        "conclusione": _conclusione_estesa(esito, bloccanti, da_verificare, dati["fonti_mancanti"]),
        "data_controllo": _dt.date.today().isoformat(),
        "liquidazione": liquidazione,
        "checklist": dati["checklist"],
        "log_fonti": dati["log_fonti"],
        "statistiche_match": dati["statistiche_match"],
        "riepilogo_corrispettivi": dati["riepilogo_corrispettivi"],
        "riepilogo_reverse": dati["riepilogo_reverse"],
        "cartella_input": str(dati["cartella_input"]),
        "cartella_output": str(dati["cartella_output"]),
        "controllo_completo": not dati["fonti_mancanti"] and liquidazione.completa,
    }


def _conclusione_operativa(esito: str, bloccanti: list[Anomalia], fonti_mancanti: list[str]) -> str:
    if esito == ESITO_SOSPENDERE:
        if fonti_mancanti:
            return ("- Conclusione: controllo **sospeso** in attesa delle fonti essenziali mancanti; "
                    "la liquidazione non e' validabile allo stato.")
        return ("- Conclusione: controllo **sospeso** per la presenza di rilievi bloccanti da "
                "rettificare prima della liquidazione.")
    if esito == ESITO_VERIFICA:
        return ("- Conclusione: liquidazione utilizzabile previa verifica professionale dei rilievi "
                "elencati al punto 4.")
    return "- Conclusione: liquidazione coerente con le fonti esaminate; nessuna rettifica proposta."


def _conclusione_estesa(esito: str, bloccanti, da_verificare, fonti_mancanti) -> str:
    voci = []
    if fonti_mancanti:
        voci.append("1. Acquisire dal cliente o dal gestionale le fonti essenziali mancanti "
                    f"({', '.join(fonti_mancanti)}) e rieseguire il controllo.")
    if bloccanti:
        voci.append(
            f"{len(voci) + 1}. Rettificare "
            + (f"il rilievo bloccante" if len(bloccanti) == 1 else f"i {len(bloccanti)} rilievi bloccanti")
            + " prima dell'invio della liquidazione."
        )
    if da_verificare:
        voci.append(
            f"{len(voci) + 1}. Sottoporre a verifica professionale "
            + (f"il rilievo da verificare" if len(da_verificare) == 1
               else f"i {len(da_verificare)} rilievi da verificare")
            + ", in particolare per gli importi con impatto IVA quantificato."
        )
    if not voci:
        voci.append("1. Nessuna azione richiesta: il controllo non ha evidenziato rilievi bloccanti "
                    "o da verificare sulle fonti esaminate.")
    voci.append(f"{len(voci) + 1}. Esito da confermare a cura del professionista incaricato.")
    return "Esito: **" + esito + "**.\n\n" + "\n".join(voci)


def _scrivi_output(contesto, dataset, anomalie, tabella_matching, quadratura, cartella_output: Path) -> None:
    """Scrive i cinque output previsti nella cartella 99_output_controllo."""
    liquidazione = contesto["liquidazione"]

    extra: dict = {}
    if tabella_matching:
        intestazioni = list(tabella_matching[0].keys())
        extra["Quadratura_XML_Registri"] = (
            intestazioni, [[r.get(c) for c in intestazioni] for r in tabella_matching]
        )
    extra["Liquidazione_ricalcolata"] = (
        ["Voce", "Importo"], [[k, v] for k, v in liquidazione.as_dict().items()]
    )
    extra["Confronto_bozza"] = (
        ["Voce", "Da fonti/registri", "Da bozza", "Differenza", "Fonte bozza"],
        [[v["voce"], v["da_fonti"], v["da_bozza"], v["differenza"], v["fonte_bozza"]] for v in quadratura],
    )
    for nome, righe in (("registro_vendite", dataset.get("registro_vendite", [])),
                        ("registro_acquisti", dataset.get("registro_acquisti", []))):
        totali = controlli.totali_per_aliquota(righe)
        if totali:
            extra[f"Totali_{nome}"] = (
                ["Aliquota", "Natura", "Imponibile", "Imposta", "Righe"],
                [[t["aliquota"], t["natura"], t["imponibile"], t["imposta"], t["righe"]] for t in totali],
            )
    if contesto["correzioni"]:
        extra["Quality_review"] = (
            ["Codice QR", "Rilievo", "Esito", "Motivo"],
            [[c["codice"], c["rilievo"], c["esito"], c["motivo"]] for c in contesto["correzioni"]],
        )

    export_outputs.esporta_anomalie(cartella_output / "anomalie_iva.xlsx", anomalie)
    export_outputs.esporta_dataset(cartella_output / "dataset_normalizzato.xlsx", dataset, extra)

    audit = {
        "controllo": {
            "cliente": contesto["cliente"],
            "anno": contesto["anno"],
            "periodo": contesto["periodo"],
            "periodicita": contesto["periodicita"],
            "partita_iva": contesto["piva"],
            "cartella_input": contesto["cartella_input"],
            "cartella_output": contesto["cartella_output"],
            "data_esecuzione": _dt.datetime.now().isoformat(timespec="seconds"),
            "versione_sistema": VERSIONE,
            "controllo_completo": contesto["controllo_completo"],
            "esito": contesto["esito"],
        },
        "cartelle_attese": contesto["checklist"]["cartelle"],
        "fonti": contesto["log_fonti"],
        "documenti_non_letti": [
            {"file": v["file"], "motivo": v["note"]}
            for v in contesto["log_fonti"] if v["esito_lettura"] == "non_letto"
        ],
        "alert_qualita_dati": [
            {"codice": a.codice, "file": a.fonte, "descrizione": a.descrizione}
            for a in anomalie if a.codice.startswith("SRC-")
        ],
        "statistiche_matching": contesto["statistiche_match"],
        "quality_review": contesto["correzioni"],
    }
    export_outputs.esporta_fonti(cartella_output / "fonti_utilizzate.json", audit)
    export_outputs.scrivi_report(
        cartella_output / "report_controllo_iva.md", contesto,
        TEMPLATE_REPORT if TEMPLATE_REPORT.is_file() else None,
    )
    export_outputs.scrivi_richiesta_documenti(
        cartella_output / "richiesta_documenti_cliente.md", contesto
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Controllo IVA periodico su cartella documentale.",
    )
    parser.add_argument("--input", required=True, help="cartella IVA/{anno}/{periodo}/{cliente}")
    parser.add_argument("--output", default=None,
                        help=f"cartella di output (default: <input>/{CARTELLA_OUTPUT})")
    parser.add_argument("--config", default=str(CONFIG_DEFAULT), help="configurazione agenti")
    parser.add_argument("--piva", default="", help="partita IVA del cliente, se non deducibile")
    parser.add_argument("--periodicita", choices=("mensile", "trimestrale"), default=None)
    parser.add_argument("--verbose", action="store_true")
    argomenti = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if argomenti.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    cartella_input = Path(argomenti.input)
    if not cartella_input.is_dir():
        log.error("cartella di input inesistente: %s", cartella_input)
        return 2
    cartella_output = Path(argomenti.output) if argomenti.output else cartella_input / CARTELLA_OUTPUT

    try:
        config = carica_config_agenti(argomenti.config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.error("configurazione agenti non caricabile: %s", exc)
        return 2

    trimestrale = None if argomenti.periodicita is None else argomenti.periodicita == "trimestrale"
    contesto = esegui_controllo(cartella_input, cartella_output, config,
                                piva_cliente=argomenti.piva, trimestrale=trimestrale)

    print(f"Cliente: {contesto['cliente']} — periodo {contesto['periodo_esteso']}")
    print(f"Esito: {contesto['esito']}")
    print(f"Controllo {'completo' if contesto['controllo_completo'] else 'LIMITATO dalle fonti disponibili'}")
    bloccanti = sum(1 for a in contesto["anomalie"] if a.gravita == GRAVITA_BLOCCANTE)
    verifica = sum(1 for a in contesto["anomalie"] if a.gravita == GRAVITA_VERIFICA)
    informative = sum(1 for a in contesto["anomalie"] if a.gravita == GRAVITA_INFO)
    print(f"Anomalie: {bloccanti} bloccanti, {verifica} da verificare, {informative} informative")
    print(f"Output in: {cartella_output}")
    return 0 if contesto["esito"] != ESITO_SOSPENDERE else 1


if __name__ == "__main__":
    raise SystemExit(main())
