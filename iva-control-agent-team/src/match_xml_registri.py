"""Quadratura fra fatture elettroniche XML e registrazioni nei registri IVA.

Il matching e' applicato in cascata dal criterio piu' forte al piu' debole; ogni
abbinamento conserva il livello usato, perche' un match debole e' un'ipotesi da
confermare, non una quadratura.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from normalize import (
    Anomalia,
    GRAVITA_BLOCCANTE,
    GRAVITA_INFO,
    GRAVITA_VERIFICA,
    IMPATTO_NON_DETERMINABILE,
    Riga,
    TOLL_DOCUMENTO,
    TOLL_RIGA,
    euro,
    quasi_uguali,
    somma,
)

AGENTE = "quadratura-xml-registri"

LIVELLI = ("esatto", "forte", "importo", "debole")


@dataclass
class Documento:
    """Aggregazione delle righe di riepilogo IVA appartenenti allo stesso documento."""

    chiave_controparte: str
    chiave_numero: str
    numero: str
    data: str
    controparte: str
    imponibile: float
    imposta: float
    totale: float | None
    righe: list[Riga]

    @property
    def fonte(self) -> str:
        return self.righe[0].fonte.testo() if self.righe else "fonte non indicata"

    @property
    def aliquote(self) -> set[float | None]:
        return {r.aliquota for r in self.righe}


def aggrega_documenti(righe: Iterable[Riga]) -> list[Documento]:
    """Raggruppa le righe per (controparte, numero, data)."""
    gruppi: dict[tuple, list[Riga]] = {}
    for riga in righe:
        chiave = (riga.chiave_controparte, riga.chiave_numero, riga.data)
        gruppi.setdefault(chiave, []).append(riga)

    documenti: list[Documento] = []
    for (controparte, numero, data), gruppo in gruppi.items():
        segno = -1 if gruppo[0].tipo_documento == "TD04" else 1
        documenti.append(
            Documento(
                chiave_controparte=controparte,
                chiave_numero=numero,
                numero=gruppo[0].numero,
                data=data,
                controparte=gruppo[0].controparte,
                imponibile=round(segno * abs(somma(r.imponibile for r in gruppo)), 2)
                if segno < 0 else somma(r.imponibile for r in gruppo),
                imposta=round(segno * abs(somma(r.imposta for r in gruppo)), 2)
                if segno < 0 else somma(r.imposta for r in gruppo),
                totale=gruppo[0].totale_documento,
                righe=gruppo,
            )
        )
    return documenti


def _giorni_tra(data_a: str, data_b: str) -> int | None:
    import datetime as dt

    try:
        return abs((dt.date.fromisoformat(data_a) - dt.date.fromisoformat(data_b)).days)
    except (ValueError, TypeError):
        return None


def _livello_match(xml: Documento, reg: Documento) -> str | None:
    if not xml.chiave_controparte or xml.chiave_controparte != reg.chiave_controparte:
        return None
    if xml.chiave_numero and xml.chiave_numero == reg.chiave_numero:
        if xml.data and xml.data == reg.data:
            return "esatto"
        distanza = _giorni_tra(xml.data, reg.data) if xml.data and reg.data else None
        if distanza is None or distanza <= 60:
            return "forte"
        return "forte"
    if xml.data and xml.data == reg.data and quasi_uguali(
        xml.imponibile, reg.imponibile, TOLL_DOCUMENTO
    ):
        return "importo"
    if quasi_uguali(xml.totale, reg.totale, TOLL_DOCUMENTO) and xml.totale is not None:
        return "debole"
    if quasi_uguali(xml.imponibile, reg.imponibile, TOLL_DOCUMENTO) and quasi_uguali(
        xml.imposta, reg.imposta, TOLL_DOCUMENTO
    ):
        return "debole"
    return None


def confronta(
    righe_xml: list[Riga],
    righe_registro: list[Riga],
    direzione: str,
    contatore: dict[str, int] | None = None,
    righe_reverse: list[Riga] | None = None,
) -> tuple[list[dict], list[Anomalia]]:
    """Confronta XML e registro di una direzione (``attive`` o ``passive``).

    ``righe_reverse`` contiene le fatture passive in inversione contabile: le
    relative integrazioni annotate nel registro vendite non hanno un XML proprio
    e non vanno qualificate come registrazioni prive di documento.

    Restituisce ``(tabella_matching, anomalie)``.
    """
    contatore = contatore if contatore is not None else {}
    documenti_xml = aggrega_documenti(righe_xml)
    documenti_reg = aggrega_documenti(righe_registro)

    abbinati_reg: set[int] = set()
    tabella: list[dict] = []
    anomalie: list[Anomalia] = []

    def codice(suffisso: str) -> str:
        """Codice progressivo per famiglia di rilievo, es. ``XML-REG-001.02``."""
        contatore[suffisso] = contatore.get(suffisso, 0) + 1
        return f"XML-REG-{suffisso}.{contatore[suffisso]:02d}"

    # Duplicati interni ai due dataset.
    for etichetta, insieme in (("XML", documenti_xml), ("registro", documenti_reg)):
        visti: dict[tuple, Documento] = {}
        for documento in insieme:
            chiave = (documento.chiave_controparte, documento.chiave_numero, documento.data)
            if not documento.chiave_numero:
                continue
            if chiave in visti:
                anomalie.append(
                    Anomalia(
                        codice=codice("003"),
                        gravita=GRAVITA_BLOCCANTE if etichetta == "registro" else GRAVITA_VERIFICA,
                        fonte=f"{documento.fonte} (cfr. {visti[chiave].fonte})",
                        descrizione=(
                            f"Documento {documento.numero} del {documento.data} di "
                            f"{documento.controparte or documento.chiave_controparte} presente due volte "
                            f"nel dataset {etichetta} ({direzione})."
                        ),
                        impatto_iva=euro(abs(documento.imposta)),
                        azione="rettifica" if etichetta == "registro" else "verifica manuale",
                        agente=AGENTE,
                    )
                )
            else:
                visti[chiave] = documento

    for documento_xml in documenti_xml:
        migliore_indice, migliore_livello = None, None
        for indice, documento_reg in enumerate(documenti_reg):
            if indice in abbinati_reg:
                continue
            livello = _livello_match(documento_xml, documento_reg)
            if livello is None:
                continue
            if migliore_livello is None or LIVELLI.index(livello) < LIVELLI.index(migliore_livello):
                migliore_indice, migliore_livello = indice, livello
            if livello == "esatto":
                break

        if migliore_indice is None:
            gravita = GRAVITA_BLOCCANTE if direzione == "attive" else GRAVITA_VERIFICA
            anomalie.append(
                Anomalia(
                    codice=codice("001"),
                    gravita=gravita,
                    fonte=documento_xml.fonte,
                    descrizione=(
                        f"Fattura {'attiva' if direzione == 'attive' else 'passiva'} n. "
                        f"{documento_xml.numero} del {documento_xml.data} "
                        f"({documento_xml.controparte or documento_xml.chiave_controparte}) presente fra gli XML "
                        f"e non rintracciata nel registro IVA."
                        + ("" if direzione == "attive" else
                           " Verificare se la detrazione e' stata rinviata al periodo di ricezione.")
                    ),
                    impatto_iva=euro(abs(documento_xml.imposta)),
                    azione="rettifica" if direzione == "attive" else "verifica manuale",
                    agente=AGENTE,
                )
            )
            tabella.append({
                "direzione": direzione, "livello_match": "nessuno",
                "numero_xml": documento_xml.numero, "data_xml": documento_xml.data,
                "controparte": documento_xml.controparte,
                "imponibile_xml": documento_xml.imponibile, "imposta_xml": documento_xml.imposta,
                "numero_registro": "", "data_registro": "",
                "imponibile_registro": None, "imposta_registro": None,
                "delta_imponibile": None, "delta_imposta": None,
                "fonte_xml": documento_xml.fonte, "fonte_registro": "",
            })
            continue

        documento_reg = documenti_reg[migliore_indice]
        abbinati_reg.add(migliore_indice)
        delta_imponibile = round(documento_xml.imponibile - documento_reg.imponibile, 2)
        delta_imposta = round(documento_xml.imposta - documento_reg.imposta, 2)

        tabella.append({
            "direzione": direzione, "livello_match": migliore_livello,
            "numero_xml": documento_xml.numero, "data_xml": documento_xml.data,
            "controparte": documento_xml.controparte or documento_reg.controparte,
            "imponibile_xml": documento_xml.imponibile, "imposta_xml": documento_xml.imposta,
            "numero_registro": documento_reg.numero, "data_registro": documento_reg.data,
            "imponibile_registro": documento_reg.imponibile, "imposta_registro": documento_reg.imposta,
            "delta_imponibile": delta_imponibile, "delta_imposta": delta_imposta,
            "fonte_xml": documento_xml.fonte, "fonte_registro": documento_reg.fonte,
        })

        integrazione = (
            any(r.reverse_charge for r in documento_xml.righe)
            and abs(documento_xml.imposta) <= TOLL_RIGA
            and abs(documento_reg.imposta) > TOLL_RIGA
            and quasi_uguali(documento_xml.imponibile, documento_reg.imponibile, TOLL_DOCUMENTO)
        )
        if integrazione:
            # Inversione contabile: l'imposta compare solo nel registro, per effetto
            # dell'integrazione del documento. Nessun rilievo di divergenza.
            anomalie.append(
                Anomalia(
                    codice=codice("009"),
                    gravita=GRAVITA_INFO,
                    fonte=f"{documento_xml.fonte}; {documento_reg.fonte}",
                    descrizione=(
                        f"Documento n. {documento_xml.numero} in inversione contabile: imposta assente "
                        f"nell'XML e integrata nel registro per {euro(documento_reg.imposta)}. "
                        "Trattamento coerente; la doppia annotazione e' verificata dall'agente "
                        "reverse-split-estero-iva."
                    ),
                    impatto_iva=euro(documento_reg.imposta),
                    azione="controllo gestionale",
                    agente=AGENTE,
                )
            )
        if not integrazione and abs(delta_imponibile) > TOLL_DOCUMENTO:
            anomalie.append(
                Anomalia(
                    codice=codice("004"),
                    gravita=GRAVITA_VERIFICA,
                    fonte=f"{documento_xml.fonte}; {documento_reg.fonte}",
                    descrizione=(
                        f"Imponibile divergente sul documento n. {documento_xml.numero}: "
                        f"XML {euro(documento_xml.imponibile)} contro registro {euro(documento_reg.imponibile)}."
                    ),
                    impatto_iva=euro(abs(delta_imposta)) if abs(delta_imposta) > TOLL_RIGA
                    else IMPATTO_NON_DETERMINABILE,
                    azione="verifica manuale",
                    agente=AGENTE,
                )
            )
        if not integrazione and abs(delta_imposta) > TOLL_RIGA:
            anomalie.append(
                Anomalia(
                    codice=codice("005"),
                    gravita=GRAVITA_BLOCCANTE if abs(delta_imposta) > TOLL_DOCUMENTO else GRAVITA_VERIFICA,
                    fonte=f"{documento_xml.fonte}; {documento_reg.fonte}",
                    descrizione=(
                        f"Imposta divergente sul documento n. {documento_xml.numero}: "
                        f"XML {euro(documento_xml.imposta)} contro registro {euro(documento_reg.imposta)}."
                    ),
                    impatto_iva=euro(abs(delta_imposta)),
                    azione="rettifica",
                    agente=AGENTE,
                )
            )
        if not integrazione and documento_xml.aliquote and documento_reg.aliquote and \
                documento_xml.aliquote != documento_reg.aliquote and \
                None not in documento_reg.aliquote:
            anomalie.append(
                Anomalia(
                    codice=codice("006"),
                    gravita=GRAVITA_VERIFICA,
                    fonte=f"{documento_xml.fonte}; {documento_reg.fonte}",
                    descrizione=(
                        f"Aliquote divergenti sul documento n. {documento_xml.numero}: XML "
                        f"{sorted(a for a in documento_xml.aliquote if a is not None)} contro registro "
                        f"{sorted(a for a in documento_reg.aliquote if a is not None)}."
                    ),
                    impatto_iva=euro(abs(delta_imposta)) if abs(delta_imposta) > TOLL_RIGA
                    else IMPATTO_NON_DETERMINABILE,
                    azione="verifica manuale",
                    agente=AGENTE,
                )
            )
        if documento_xml.data and documento_reg.data and documento_xml.data[:7] != documento_reg.data[:7]:
            anomalie.append(
                Anomalia(
                    codice=codice("007"),
                    gravita=GRAVITA_VERIFICA,
                    fonte=f"{documento_xml.fonte}; {documento_reg.fonte}",
                    descrizione=(
                        f"Documento n. {documento_xml.numero} datato {documento_xml.data} negli XML e "
                        f"{documento_reg.data} nel registro: differenza di competenza da verificare."
                    ),
                    impatto_iva=euro(abs(documento_xml.imposta)),
                    azione="verifica manuale",
                    agente=AGENTE,
                )
            )
        if migliore_livello == "debole":
            anomalie.append(
                Anomalia(
                    codice=codice("008"),
                    gravita=GRAVITA_INFO,
                    fonte=f"{documento_xml.fonte}; {documento_reg.fonte}",
                    descrizione=(
                        f"Abbinamento debole (solo per importo) fra XML n. {documento_xml.numero} e "
                        f"registrazione n. {documento_reg.numero}: numero documento non coincidente."
                    ),
                    impatto_iva=IMPATTO_NON_DETERMINABILE,
                    azione="verifica manuale",
                    agente=AGENTE,
                )
            )

    for indice, documento_reg in enumerate(documenti_reg):
        if indice in abbinati_reg:
            continue
        tabella.append({
            "direzione": direzione, "livello_match": "nessuno",
            "numero_xml": "", "data_xml": "", "controparte": documento_reg.controparte,
            "imponibile_xml": None, "imposta_xml": None,
            "numero_registro": documento_reg.numero, "data_registro": documento_reg.data,
            "imponibile_registro": documento_reg.imponibile, "imposta_registro": documento_reg.imposta,
            "delta_imponibile": None, "delta_imposta": None,
            "fonte_xml": "", "fonte_registro": documento_reg.fonte,
        })
        integrazione_reverse = next(
            (
                r for r in (righe_reverse or [])
                if r.chiave_controparte == documento_reg.chiave_controparte
                and quasi_uguali(r.imponibile, documento_reg.imponibile, TOLL_DOCUMENTO)
            ),
            None,
        )
        if integrazione_reverse is not None:
            anomalie.append(
                Anomalia(
                    codice=codice("009"),
                    gravita=GRAVITA_INFO,
                    fonte=f"{documento_reg.fonte}; {integrazione_reverse.fonte.testo()}",
                    descrizione=(
                        f"Registrazione n. {documento_reg.numero} riconducibile all'integrazione della "
                        f"fattura in inversione contabile n. {integrazione_reverse.numero} di "
                        f"{integrazione_reverse.controparte or integrazione_reverse.piva}: "
                        "l'assenza di un XML proprio e' fisiologica."
                    ),
                    impatto_iva=euro(abs(documento_reg.imposta)),
                    azione="controllo gestionale",
                    agente=AGENTE,
                )
            )
            continue

        anomalie.append(
            Anomalia(
                codice=codice("002"),
                gravita=GRAVITA_VERIFICA,
                fonte=documento_reg.fonte,
                descrizione=(
                    f"Registrazione n. {documento_reg.numero} del {documento_reg.data} "
                    f"({documento_reg.controparte or documento_reg.chiave_controparte}) priva di XML corrispondente. "
                    "Verificare se si tratta di documento legittimamente non elettronico "
                    "(fattura estera, bolletta doganale, autofattura) prima di qualificarla come anomalia."
                ),
                impatto_iva=euro(abs(documento_reg.imposta)),
                azione="richiesta documento",
                agente=AGENTE,
            )
        )

    return tabella, anomalie


def statistiche(tabella: list[dict]) -> dict:
    """Indicatori di copertura del matching."""
    totale = len(tabella)
    abbinati = [r for r in tabella if r["livello_match"] not in ("nessuno",)]
    return {
        "documenti_confrontati": totale,
        "abbinati": len(abbinati),
        "copertura_perc": round(100 * len(abbinati) / totale, 1) if totale else 0.0,
        "per_livello": {
            livello: sum(1 for r in tabella if r["livello_match"] == livello) for livello in LIVELLI
        },
        "solo_xml": sum(1 for r in tabella if r["livello_match"] == "nessuno" and r["numero_xml"]),
        "solo_registro": sum(1 for r in tabella if r["livello_match"] == "nessuno" and r["numero_registro"]),
    }
