"""Lettura di registri e prospetti in formato Excel (.xlsx) e CSV/TSV.

Il riconoscimento delle colonne e' basato su sinonimi: i gestionali italiani
usano intestazioni diverse per gli stessi concetti. Quando un'intestazione non
e' riconosciuta la colonna viene conservata come dato grezzo e segnalata,
mai reinterpretata a caso.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Iterator

from normalize import (
    Fonte,
    Riga,
    normalizza_natura,
    parse_aliquota,
    parse_data,
    parse_importo,
)

try:  # openpyxl e' opzionale: senza di esso restano leggibili i soli CSV
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover - dipende dall'ambiente
    load_workbook = None


ESTENSIONI_TABELLARI = {".xlsx", ".xlsm", ".csv", ".tsv", ".txt"}

#: Sinonimi di intestazione -> campo canonico.
SINONIMI: dict[str, tuple[str, ...]] = {
    "data": ("data", "data documento", "data fattura", "data doc", "data emissione", "data corrispettivo", "giorno"),
    "data_registrazione": ("data registrazione", "data reg", "data protocollo", "data annotazione"),
    "data_ricezione": ("data ricezione", "data consegna", "data ricezione sdi", "data sdi"),
    "numero": ("numero", "n. doc", "n doc", "numero documento", "num documento", "nr documento",
               "n. fattura", "numero fattura", "protocollo", "n. protocollo", "prot"),
    "tipo_documento": ("tipo documento", "tipo doc", "tipodoc", "td", "tipo"),
    "controparte": ("cliente", "fornitore", "denominazione", "ragione sociale", "controparte",
                    "cliente/fornitore", "soggetto", "descrizione soggetto"),
    "piva": ("partita iva", "p.iva", "p iva", "piva", "partitaiva", "id fiscale iva"),
    "cf": ("codice fiscale", "cod. fiscale", "cod fiscale", "cf"),
    "imponibile": ("imponibile", "imponibile iva", "totale imponibile", "base imponibile", "imp."),
    "imposta": ("imposta", "iva", "importo iva", "totale iva", "imposta iva"),
    "aliquota": ("aliquota", "aliquota iva", "aliq", "aliq.", "%iva", "% iva", "perc iva"),
    "natura": ("natura", "natura iva", "codice natura", "esenzione"),
    "totale_documento": ("totale documento", "totale", "totale fattura", "importo totale", "lordo",
                         "totale corrispettivi", "corrispettivo", "incasso"),
    "sezionale": ("sezionale", "registro", "serie", "sez", "sez."),
    "descrizione": ("descrizione", "causale", "oggetto", "conto", "descrizione conto", "note"),
    "indetraibile": ("iva indetraibile", "indetraibile", "imposta indetraibile", "% indetraibilita"),
    "esigibilita": ("esigibilita", "esigibilita iva", "esigibilita' iva"),
}

#: Righe di totale/riporto da escludere dal corpo dati.
_RE_RIGA_TOTALE = re.compile(
    r"^\s*(totale|totali|tot\.|progressivo|progressivi|riporto|a riportare|riepilogo|"
    r"saldo|di cui)\b", re.IGNORECASE
)


class ErroreLettura(Exception):
    """Sollevata quando il file tabellare non e' interpretabile."""


def _pulisci_intestazione(valore: Any) -> str:
    testo = str(valore or "").strip().lower()
    testo = testo.replace("\n", " ").replace("_", " ")
    testo = re.sub(r"[^a-z0-9%.' ]", " ", testo)
    return re.sub(r"\s+", " ", testo).strip()


def mappa_intestazioni(intestazioni: list[Any]) -> tuple[dict[int, str], list[str]]:
    """Associa gli indici di colonna ai campi canonici.

    Restituisce ``(mappa, non_riconosciute)``.
    """
    mappa: dict[int, str] = {}
    non_riconosciute: list[str] = []
    usati: set[str] = set()
    for indice, grezza in enumerate(intestazioni):
        pulita = _pulisci_intestazione(grezza)
        if not pulita:
            continue
        campo = None
        for canonico, sinonimi in SINONIMI.items():
            if pulita in sinonimi:
                campo = canonico
                break
        if campo is None:
            for canonico, sinonimi in SINONIMI.items():
                if any(pulita.startswith(s) or s in pulita for s in sinonimi):
                    campo = canonico
                    break
        if campo is None or campo in usati:
            non_riconosciute.append(str(grezza))
            continue
        mappa[indice] = campo
        usati.add(campo)
    return mappa, non_riconosciute


def _righe_csv(percorso: Path) -> Iterator[list[Any]]:
    with percorso.open("r", encoding="utf-8-sig", newline="") as handle:
        campione = handle.read(4096)
        handle.seek(0)
        try:
            dialetto = csv.Sniffer().sniff(campione, delimiters=";,\t|")
        except csv.Error:
            dialetto = csv.excel
            dialetto.delimiter = ";" if campione.count(";") >= campione.count(",") else ","
        for riga in csv.reader(handle, dialetto):
            yield riga


def _righe_xlsx(percorso: Path, foglio: str | None = None) -> Iterator[tuple[str, list[Any]]]:
    if load_workbook is None:
        raise ErroreLettura("openpyxl non disponibile: impossibile leggere .xlsx")
    workbook = load_workbook(percorso, data_only=True, read_only=True)
    try:
        fogli = [workbook[foglio]] if foglio else workbook.worksheets
        for ws in fogli:
            for riga in ws.iter_rows(values_only=True):
                yield ws.title, list(riga)
    finally:
        workbook.close()


def _trova_intestazione(righe: list[list[Any]], massimo: int = 15) -> int:
    """Individua l'indice della riga di intestazione (la piu' ricca di campi noti)."""
    migliore, punteggio_migliore = -1, 0
    for indice, riga in enumerate(righe[:massimo]):
        mappa, _ = mappa_intestazioni(riga)
        punteggio = len(mappa)
        if punteggio > punteggio_migliore:
            migliore, punteggio_migliore = indice, punteggio
    return migliore if punteggio_migliore >= 2 else -1


def leggi_tabella(percorso: Path, origine: str) -> tuple[list[Riga], dict]:
    """Legge un file tabellare e restituisce ``(righe normalizzate, log)``."""
    log = {"file": str(percorso), "righe": 0, "esito": "letto", "note": "",
           "colonne_non_riconosciute": []}
    estensione = percorso.suffix.lower()

    blocchi: list[tuple[str, list[list[Any]]]] = []
    if estensione in {".csv", ".tsv", ".txt"}:
        blocchi.append(("", [list(r) for r in _righe_csv(percorso)]))
    elif estensione in {".xlsx", ".xlsm"}:
        per_foglio: dict[str, list[list[Any]]] = {}
        for nome_foglio, riga in _righe_xlsx(percorso):
            per_foglio.setdefault(nome_foglio, []).append(riga)
        blocchi.extend(per_foglio.items())
    else:
        raise ErroreLettura(f"estensione non gestita: {estensione}")

    righe_out: list[Riga] = []
    non_riconosciute: list[str] = []
    fogli_saltati: list[str] = []

    for nome_foglio, righe in blocchi:
        if not righe:
            continue
        indice_intestazione = _trova_intestazione(righe)
        if indice_intestazione < 0:
            fogli_saltati.append(nome_foglio or percorso.name)
            continue
        mappa, ignote = mappa_intestazioni(righe[indice_intestazione])
        non_riconosciute.extend(ignote)

        for offset, riga in enumerate(righe[indice_intestazione + 1:], start=indice_intestazione + 2):
            if not any(cella not in (None, "") for cella in riga):
                continue
            testo_riga = " ".join(str(c) for c in riga if c is not None)
            if _RE_RIGA_TOTALE.match(testo_riga.strip()):
                continue
            valori = {campo: riga[i] if i < len(riga) else None for i, campo in mappa.items()}
            if _RE_RIGA_TOTALE.match(str(valori.get("controparte") or "")) or _RE_RIGA_TOTALE.match(
                str(valori.get("descrizione") or "")
            ):
                continue

            imponibile = parse_importo(valori.get("imponibile"))
            imposta = parse_importo(valori.get("imposta"))
            totale = parse_importo(valori.get("totale_documento"))
            if imponibile is None and imposta is None and totale is None:
                continue  # riga senza contenuto contabile

            data = parse_data(valori.get("data"))
            righe_out.append(
                Riga(
                    origine=origine,
                    tipo_documento=str(valori.get("tipo_documento") or "").strip().upper(),
                    numero=str(valori.get("numero") or "").strip(),
                    data=data or "",
                    data_registrazione=parse_data(valori.get("data_registrazione")) or "",
                    data_ricezione=parse_data(valori.get("data_ricezione")) or "",
                    controparte=str(valori.get("controparte") or "").strip(),
                    piva=str(valori.get("piva") or "").strip(),
                    cf=str(valori.get("cf") or "").strip(),
                    imponibile=imponibile,
                    aliquota=parse_aliquota(valori.get("aliquota")),
                    imposta=imposta,
                    natura=normalizza_natura(valori.get("natura")),
                    esigibilita=str(valori.get("esigibilita") or "").strip().upper()[:1],
                    indetraibile=parse_importo(valori.get("indetraibile")),
                    totale_documento=totale,
                    sezionale=str(valori.get("sezionale") or "").strip(),
                    descrizione=str(valori.get("descrizione") or "").strip(),
                    competenza=(data or "")[:7],
                    fonte=Fonte(
                        file=percorso.name,
                        foglio=nome_foglio,
                        riga=offset,
                        numero_documento=str(valori.get("numero") or "").strip(),
                    ),
                )
            )

    log["righe"] = len(righe_out)
    log["colonne_non_riconosciute"] = sorted(set(non_riconosciute))
    note = []
    if fogli_saltati:
        note.append("intestazioni non riconosciute in: " + ", ".join(fogli_saltati))
        log["esito"] = "letto_parzialmente" if righe_out else "non_letto"
    if non_riconosciute:
        note.append("colonne ignorate: " + ", ".join(sorted(set(non_riconosciute))[:10]))
    if not righe_out and not note:
        note.append("nessuna riga contabile individuata")
        log["esito"] = "letto_parzialmente"
    log["note"] = "; ".join(note)
    return righe_out, log
