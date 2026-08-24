"""Estrazione di testo e importi da PDF (registri, bozze di liquidazione, F24).

L'estrazione da PDF e' intrinsecamente meno affidabile della lettura di un file
strutturato: questo modulo dichiara sempre il proprio livello di confidenza e
non produce righe contabili "indovinate". Se il PDF non contiene testo (scansione)
l'esito e' ``non_letto`` con richiesta del file nativo o di OCR.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from normalize import Fonte, Riga, parse_aliquota, parse_data, parse_importo

_BACKEND = None
# L'import delle librerie PDF puo' fallire non solo per assenza del pacchetto ma
# anche per dipendenze native rotte: intercettiamo qualsiasi eccezione, perche'
# un backend non importabile equivale a un backend assente.
try:  # backend preferito: estrazione testuale con layout
    import pdfplumber  # type: ignore

    _BACKEND = "pdfplumber"
except BaseException:  # pragma: no cover - dipende dall'ambiente
    try:
        from pypdf import PdfReader  # type: ignore

        _BACKEND = "pypdf"
    except BaseException:
        _BACKEND = None


class ErroreLettura(Exception):
    """Sollevata quando il PDF non e' interpretabile."""


def backend_disponibile() -> str | None:
    """Nome della libreria disponibile per l'estrazione, o ``None``."""
    return _BACKEND


def estrai_testo(percorso: Path) -> list[str]:
    """Restituisce il testo per pagina. Solleva ``ErroreLettura`` se non estraibile."""
    if _BACKEND is None:
        raise ErroreLettura(
            "nessun backend PDF installato (pdfplumber o pypdf): file non letto"
        )
    pagine: list[str] = []
    try:
        if _BACKEND == "pdfplumber":
            import pdfplumber  # type: ignore

            with pdfplumber.open(percorso) as pdf:
                pagine = [(pagina.extract_text() or "") for pagina in pdf.pages]
        else:
            from pypdf import PdfReader  # type: ignore

            lettore = PdfReader(str(percorso))
            pagine = [(pagina.extract_text() or "") for pagina in lettore.pages]
    except Exception as exc:  # il backend puo' sollevare eccezioni proprie
        raise ErroreLettura(f"estrazione fallita: {exc}") from exc

    if not any(p.strip() for p in pagine):
        raise ErroreLettura(
            "PDF privo di testo estraibile (probabile scansione): richiedere file nativo o OCR"
        )
    return pagine


# ---------------------------------------------------------------------------
# Estrazione di voci "etichetta -> importo"
# ---------------------------------------------------------------------------

#: Etichette tipiche delle bozze di liquidazione dei gestionali italiani.
ETICHETTE_LIQUIDAZIONE: dict[str, tuple[str, ...]] = {
    "iva_vendite": ("iva vendite", "iva su vendite", "iva a debito", "iva esigibile",
                    "totale iva vendite", "iva operazioni attive"),
    "iva_corrispettivi": ("iva corrispettivi", "iva su corrispettivi", "corrispettivi"),
    "iva_acquisti": ("iva acquisti", "iva su acquisti", "iva a credito", "iva detraibile",
                     "totale iva acquisti", "iva operazioni passive"),
    "credito_precedente": ("credito precedente", "credito periodo precedente", "credito riportato",
                           "iva a credito periodo precedente", "riporto credito"),
    "debito_precedente": ("debito precedente", "debito periodo precedente", "debito riportato"),
    "acconto": ("acconto", "acconto iva", "acconto dicembre"),
    "interessi": ("interessi", "interessi 1%", "interessi trimestrali"),
    "saldo": ("iva da versare", "totale da versare", "saldo del periodo", "debito del periodo",
              "credito del periodo", "iva a debito del periodo", "importo da versare"),
}

_RE_IMPORTO = re.compile(r"-?\(?\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})\)?-?|-?\d+,\d{2}|-?\d+\.\d{2}")


def estrai_voci(pagine: list[str], etichette: dict[str, tuple[str, ...]] | None = None) -> dict[str, dict]:
    """Cerca coppie etichetta/importo nel testo del PDF.

    Restituisce ``{campo: {"importo": float, "pagina": int, "riga": str}}``.
    Un campo assente significa "non trovato", non "zero".
    """
    etichette = etichette or ETICHETTE_LIQUIDAZIONE
    trovati: dict[str, dict] = {}
    for numero_pagina, testo in enumerate(pagine, start=1):
        for riga in testo.splitlines():
            pulita = re.sub(r"\s+", " ", riga).strip()
            if not pulita:
                continue
            minuscola = pulita.lower()
            for campo, varianti in etichette.items():
                if campo in trovati:
                    continue
                if not any(v in minuscola for v in varianti):
                    continue
                importi = _RE_IMPORTO.findall(pulita)
                if not importi:
                    continue
                valore = parse_importo(importi[-1])
                if valore is None:
                    continue
                if "credito" in minuscola and campo == "saldo" and valore > 0:
                    valore = -valore  # un credito e' un saldo negativo
                trovati[campo] = {"importo": valore, "pagina": numero_pagina, "riga": pulita}
    return trovati


def leggi_bozza_liquidazione(percorso: Path) -> tuple[dict[str, dict], dict]:
    """Legge una bozza di liquidazione in PDF e ne estrae le voci principali."""
    log = {"file": str(percorso), "righe": 0, "esito": "letto", "note": "",
           "backend": _BACKEND or "assente"}
    try:
        pagine = estrai_testo(percorso)
    except ErroreLettura as exc:
        log.update(esito="non_letto", note=str(exc))
        return {}, log

    voci = estrai_voci(pagine)
    log["righe"] = len(voci)
    if not voci:
        log.update(esito="letto_parzialmente",
                   note="testo estratto ma nessuna voce di liquidazione riconosciuta")
    else:
        mancanti = [c for c in ("iva_vendite", "iva_acquisti", "saldo") if c not in voci]
        if mancanti:
            log.update(esito="letto_parzialmente",
                       note="voci non individuate: " + ", ".join(mancanti))
    return voci, log


def leggi_registro_pdf(percorso: Path, origine: str) -> tuple[list[Riga], dict]:
    """Estrae righe di registro da un PDF testuale, con confidenza dichiarata.

    L'euristica riconosce righe che contengono data, numero e almeno due importi.
    Le righe non riconosciute non vengono forzate: sono contate e segnalate.
    """
    log = {"file": str(percorso), "righe": 0, "esito": "letto", "note": "",
           "backend": _BACKEND or "assente", "righe_non_interpretate": 0}
    try:
        pagine = estrai_testo(percorso)
    except ErroreLettura as exc:
        log.update(esito="non_letto", note=str(exc))
        return [], log

    righe: list[Riga] = []
    non_interpretate = 0
    re_data = re.compile(r"\b(\d{2}[/.-]\d{2}[/.-]\d{2,4})\b")

    for numero_pagina, testo in enumerate(pagine, start=1):
        for numero_riga, riga_grezza in enumerate(testo.splitlines(), start=1):
            riga = re.sub(r"\s+", " ", riga_grezza).strip()
            if not riga or len(riga) < 12:
                continue
            date = re_data.findall(riga)
            importi = [parse_importo(i) for i in _RE_IMPORTO.findall(riga)]
            importi = [i for i in importi if i is not None]
            if not date or len(importi) < 2:
                if date or len(importi) >= 1:
                    non_interpretate += 1
                continue

            # Convenzione prudenziale: gli ultimi importi della riga sono
            # imponibile e imposta; l'aliquota, se presente, e' un intero <= 30.
            aliquota = None
            for valore in importi:
                if valore is not None and float(valore).is_integer() and 0 < valore <= 30:
                    aliquota = parse_aliquota(valore)
                    break
            imponibile, imposta = importi[-2], importi[-1]
            numero = ""
            match_numero = re.search(r"\b(\d{1,6}(?:/\w+)?)\b", riga)
            if match_numero:
                numero = match_numero.group(1)

            righe.append(
                Riga(
                    origine=origine,
                    numero=numero,
                    data=parse_data(date[0]) or "",
                    imponibile=imponibile,
                    imposta=imposta,
                    aliquota=aliquota,
                    descrizione=riga[:200],
                    competenza=(parse_data(date[0]) or "")[:7],
                    fonte=Fonte(file=percorso.name, pagina=numero_pagina, riga=numero_riga,
                                numero_documento=numero),
                    note="estrazione da PDF: dato da verificare",
                )
            )

    log["righe"] = len(righe)
    log["righe_non_interpretate"] = non_interpretate
    if non_interpretate:
        log["esito"] = "letto_parzialmente"
        log["note"] = f"{non_interpretate} righe con dati parziali non interpretate"
    if not righe:
        log["esito"] = "letto_parzialmente"
        log["note"] = (log["note"] + "; " if log["note"] else "") + \
            "nessuna riga di registro riconosciuta nel testo estratto"
    return righe, log
