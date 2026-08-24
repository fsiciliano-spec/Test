"""Parsing delle fatture elettroniche FatturaPA (.xml e .xml.p7m).

Il dato IVA di riferimento e' ``DatiRiepilogo``: una riga per coppia
aliquota/natura. Le linee di dettaglio vengono usate solo per la descrizione
(che alimenta i controlli di detraibilita').
"""

from __future__ import annotations

import base64
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from normalize import (
    Fonte,
    Riga,
    TIPI_DOCUMENTO_REVERSE,
    normalizza_natura,
    normalizza_piva,
    parse_aliquota,
    parse_data,
    parse_importo,
    somma,
)

_RE_XML_INIZIO = re.compile(rb"<\?xml[^>]*\?>")
_RE_FATTURA_FINE = re.compile(rb"</(?:\w+:)?FatturaElettronica\s*>")
_RE_FATTURA_INIZIO = re.compile(rb"<(?:\w+:)?FatturaElettronica[\s>]")


class ErroreParsing(Exception):
    """Sollevata quando il file non e' interpretabile come FatturaPA."""


# ---------------------------------------------------------------------------
# Estrazione del contenuto XML
# ---------------------------------------------------------------------------

def estrai_xml(percorso: Path) -> bytes:
    """Restituisce i byte XML di una fattura, scompattando la busta P7M se presente.

    Il file ``.p7m`` e' una busta CAdES (DER o base64) che contiene l'XML in
    chiaro: individuiamo l'intervallo fra il tag di apertura e quello di
    chiusura senza dipendere da librerie crittografiche, perche' qui non serve
    verificare la firma ma leggere il contenuto.
    """
    dati = percorso.read_bytes()
    if percorso.suffix.lower() != ".p7m":
        return dati

    for candidato in (dati, _prova_base64(dati)):
        if candidato is None:
            continue
        estratto = _ritaglia_fattura(candidato)
        if estratto is not None:
            return estratto
    raise ErroreParsing("busta P7M non scompattabile: XML non individuato")


def _prova_base64(dati: bytes) -> bytes | None:
    testo = b"".join(dati.split())
    if not re.fullmatch(rb"[A-Za-z0-9+/=]+", testo or b"x"):
        return None
    try:
        return base64.b64decode(testo, validate=True)
    except Exception:
        return None


def _ritaglia_fattura(dati: bytes) -> bytes | None:
    fine = _RE_FATTURA_FINE.search(dati)
    if not fine:
        return None
    inizio_tag = _RE_FATTURA_INIZIO.search(dati, 0, fine.start())
    if not inizio_tag:
        return None
    dichiarazione = _RE_XML_INIZIO.search(dati, 0, inizio_tag.start())
    inizio = dichiarazione.start() if dichiarazione else inizio_tag.start()
    return dati[inizio:fine.end()]


def _senza_namespace(elemento: ET.Element) -> ET.Element:
    for nodo in elemento.iter():
        if "}" in nodo.tag:
            nodo.tag = nodo.tag.split("}", 1)[1]
    return elemento


def _testo(nodo: ET.Element | None, percorso: str, default: str = "") -> str:
    if nodo is None:
        return default
    trovato = nodo.find(percorso)
    if trovato is None or trovato.text is None:
        return default
    return trovato.text.strip()


# ---------------------------------------------------------------------------
# Parsing della fattura
# ---------------------------------------------------------------------------

def leggi_fattura(percorso: Path, piva_cliente: str = "") -> tuple[list[Riga], list[str]]:
    """Legge un file fattura e restituisce ``(righe, avvisi)``.

    ``piva_cliente`` serve a stabilire se il documento e' attivo o passivo; se
    non e' nota, l'origine resta ``xml_indeterminata`` e l'agente Orchestratore
    deve risolvere l'ambiguita'.
    """
    avvisi: list[str] = []
    try:
        contenuto = estrai_xml(percorso)
    except OSError as exc:
        raise ErroreParsing(f"file non leggibile: {exc}") from exc

    try:
        radice = _senza_namespace(ET.fromstring(contenuto))
    except ET.ParseError as exc:
        raise ErroreParsing(f"XML non valido: {exc}") from exc

    header = radice.find("FatturaElettronicaHeader")
    cedente = header.find("CedentePrestatore/DatiAnagrafici") if header is not None else None
    cessionario = header.find("CessionarioCommittente/DatiAnagrafici") if header is not None else None

    cedente_piva = _testo(cedente, "IdFiscaleIVA/IdCodice")
    cedente_cf = _testo(cedente, "CodiceFiscale")
    cedente_nome = _testo(cedente, "Anagrafica/Denominazione") or " ".join(
        x for x in (_testo(cedente, "Anagrafica/Nome"), _testo(cedente, "Anagrafica/Cognome")) if x
    )
    cess_piva = _testo(cessionario, "IdFiscaleIVA/IdCodice")
    cess_cf = _testo(cessionario, "CodiceFiscale")
    cess_nome = _testo(cessionario, "Anagrafica/Denominazione") or " ".join(
        x for x in (_testo(cessionario, "Anagrafica/Nome"), _testo(cessionario, "Anagrafica/Cognome")) if x
    )

    chiave_cliente = normalizza_piva(piva_cliente)
    corpi = radice.findall("FatturaElettronicaBody")
    if not corpi:
        raise ErroreParsing("FatturaElettronicaBody assente")
    if len(corpi) > 1:
        avvisi.append(f"file con {len(corpi)} corpi fattura: trattati come documenti distinti")

    righe: list[Riga] = []
    for indice, corpo in enumerate(corpi, start=1):
        generali = corpo.find("DatiGenerali/DatiGeneraliDocumento")
        tipo = _testo(generali, "TipoDocumento").upper()
        numero = _testo(generali, "Numero")
        data = parse_data(_testo(generali, "Data"))
        divisa = _testo(generali, "Divisa").upper()
        totale = parse_importo(_testo(generali, "ImportoTotaleDocumento") or None)

        if divisa and divisa != "EUR":
            avvisi.append(f"documento {numero}: divisa {divisa} diversa da EUR")

        collegate = [
            _testo(nodo, "IdDocumento")
            for nodo in corpo.findall("DatiGenerali/DatiFattureCollegate")
        ]
        descrizioni = [
            _testo(linea, "Descrizione")
            for linea in corpo.findall("DatiBeniServizi/DettaglioLinee")
        ]
        descrizione = " | ".join(d for d in descrizioni if d)[:500]

        if chiave_cliente and normalizza_piva(cedente_piva) == chiave_cliente:
            origine, controparte = "xml_attiva", (cess_nome, cess_piva, cess_cf)
        elif chiave_cliente and (
            normalizza_piva(cess_piva) == chiave_cliente or (cess_cf and cess_cf.upper() == piva_cliente.upper())
        ):
            origine, controparte = "xml_passiva", (cedente_nome, cedente_piva, cedente_cf)
        else:
            origine, controparte = "xml_indeterminata", (cedente_nome, cedente_piva, cedente_cf)
            if chiave_cliente:
                avvisi.append(
                    f"documento {numero}: nessuna delle controparti coincide con la P.IVA del cliente"
                )

        riepiloghi = corpo.findall("DatiBeniServizi/DatiRiepilogo")
        if not riepiloghi:
            avvisi.append(f"documento {numero}: DatiRiepilogo assente, riga non generata")
            continue

        for progressivo, riepilogo in enumerate(riepiloghi, start=1):
            aliquota = parse_aliquota(_testo(riepilogo, "AliquotaIVA"))
            imponibile = parse_importo(_testo(riepilogo, "ImponibileImporto"))
            imposta = parse_importo(_testo(riepilogo, "Imposta"))
            natura = normalizza_natura(_testo(riepilogo, "Natura"))
            esigibilita = _testo(riepilogo, "EsigibilitaIVA").upper()

            righe.append(
                Riga(
                    origine=origine,
                    tipo_documento=tipo,
                    numero=numero,
                    data=data or "",
                    controparte=controparte[0],
                    piva=controparte[1],
                    cf=controparte[2],
                    imponibile=imponibile,
                    aliquota=aliquota,
                    imposta=imposta,
                    natura=natura,
                    esigibilita=esigibilita,
                    split_payment=esigibilita == "S",
                    reverse_charge=tipo in TIPI_DOCUMENTO_REVERSE or natura.startswith("N6"),
                    totale_documento=totale,
                    descrizione=descrizione,
                    competenza=(data or "")[:7],
                    fonte=Fonte(
                        file=percorso.name,
                        riga=progressivo,
                        numero_documento=numero,
                    ),
                    note="; ".join(f"collegata a {c}" for c in collegate if c),
                )
            )
    return righe, avvisi


def leggi_cartella(cartella: Path, piva_cliente: str = "") -> tuple[list[Riga], list[dict]]:
    """Legge tutte le fatture di una cartella.

    Restituisce le righe normalizzate e un log per file con esito di lettura.
    """
    righe: list[Riga] = []
    log: list[dict] = []
    if not cartella.is_dir():
        return righe, log

    for percorso in sorted(cartella.rglob("*")):
        if not percorso.is_file():
            continue
        if percorso.suffix.lower() not in {".xml", ".p7m"}:
            continue
        voce = {"file": str(percorso), "righe": 0, "esito": "letto", "note": ""}
        try:
            estratte, avvisi = leggi_fattura(percorso, piva_cliente)
        except ErroreParsing as exc:
            voce.update(esito="non_letto", note=str(exc))
        else:
            righe.extend(estratte)
            voce["righe"] = len(estratte)
            if avvisi:
                voce["esito"] = "letto_parzialmente" if not estratte else "letto"
                voce["note"] = "; ".join(avvisi)
        log.append(voce)
    return righe, log


def riepilogo_per_aliquota(righe: list[Riga]) -> list[dict]:
    """Aggrega imponibile e imposta per origine, aliquota e natura."""
    aggregato: dict[tuple, dict] = {}
    for riga in righe:
        chiave = (riga.origine, riga.aliquota, riga.natura)
        voce = aggregato.setdefault(
            chiave,
            {"origine": riga.origine, "aliquota": riga.aliquota, "natura": riga.natura,
             "imponibile": 0.0, "imposta": 0.0, "documenti": 0},
        )
        voce["imponibile"] = somma([voce["imponibile"], riga.imponibile])
        voce["imposta"] = somma([voce["imposta"], riga.imposta])
        voce["documenti"] += 1
    return sorted(aggregato.values(), key=lambda v: (v["origine"], v["aliquota"] or -1, v["natura"]))
