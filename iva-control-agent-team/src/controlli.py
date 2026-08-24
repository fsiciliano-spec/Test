"""Controlli tematici degli agenti specialistici.

Ogni funzione riproduce, in forma deterministica, i controlli che l'agente
corrispondente esegue: il codice copre i controlli quantitativi e ripetibili,
l'agente aggiunge il giudizio sui casi che richiedono valutazione professionale.
"""

from __future__ import annotations

import datetime as _dt
import re
import statistics
from collections import defaultdict

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

# ---------------------------------------------------------------------------
# Utilita'
# ---------------------------------------------------------------------------

class Contatore:
    """Genera codici anomalia progressivi per famiglia (``REG-004.02``)."""

    def __init__(self, prefisso: str) -> None:
        self.prefisso = prefisso
        self._conteggi: dict[str, int] = defaultdict(int)

    def __call__(self, suffisso: str) -> str:
        self._conteggi[suffisso] += 1
        return f"{self.prefisso}-{suffisso}.{self._conteggi[suffisso]:02d}"


# ---------------------------------------------------------------------------
# Registri IVA
# ---------------------------------------------------------------------------

def controlla_registri(
    righe: list[Riga],
    mesi_periodo: list[str],
    etichetta: str,
    controlla_numerazione: bool = False,
) -> list[Anomalia]:
    """Controlli su numerazione, date, quadratura riga e duplicati.

    ``controlla_numerazione`` va attivato solo sui registri a numerazione
    progressiva propria (vendite, corrispettivi): nel registro acquisti il
    numero e' quello del documento del fornitore e i salti sono fisiologici.
    """
    anomalie: list[Anomalia] = []
    codice = Contatore("REG")
    agente = "registri-iva"

    visti: dict[tuple, Riga] = {}
    numeri_per_sezionale: dict[str, list[tuple[int, Riga]]] = defaultdict(list)

    for riga in righe:
        # Quadratura di riga: imponibile x aliquota = imposta.
        if riga.imponibile is not None and riga.aliquota is not None and riga.imposta is not None:
            attesa = round(riga.imponibile * riga.aliquota / 100, 2)
            if not quasi_uguali(attesa, riga.imposta, TOLL_RIGA):
                anomalie.append(Anomalia(
                    codice=codice("004"),
                    gravita=GRAVITA_VERIFICA if abs(attesa - riga.imposta) <= TOLL_DOCUMENTO
                    else GRAVITA_BLOCCANTE,
                    fonte=riga.fonte.testo(),
                    descrizione=(
                        f"{etichetta}: imposta esposta {euro(riga.imposta)} contro "
                        f"{euro(attesa)} attesa da imponibile {euro(riga.imponibile)} "
                        f"per aliquota {riga.aliquota:g}%."
                    ),
                    impatto_iva=euro(abs(round(attesa - riga.imposta, 2))),
                    azione="rettifica",
                    agente=agente,
                ))

        # Natura e imposta contemporaneamente valorizzate.
        if riga.natura and riga.imposta not in (None, 0.0) and abs(riga.imposta or 0) > TOLL_RIGA:
            anomalie.append(Anomalia(
                codice=codice("007"),
                gravita=GRAVITA_VERIFICA,
                fonte=riga.fonte.testo(),
                descrizione=(
                    f"{etichetta}: riga con natura {riga.natura} e imposta {euro(riga.imposta)} "
                    "contemporaneamente valorizzate."
                ),
                impatto_iva=euro(abs(riga.imposta)),
                azione="verifica manuale",
                agente=agente,
            ))

        # Competenza.
        riferimento = riga.data_registrazione or riga.data
        if mesi_periodo and riferimento and riferimento[:7] not in mesi_periodo:
            anomalie.append(Anomalia(
                codice=codice("003"),
                gravita=GRAVITA_VERIFICA,
                fonte=riga.fonte.testo(),
                descrizione=(
                    f"{etichetta}: documento n. {riga.numero or 's.n.'} con data {riferimento}, "
                    f"fuori dal periodo di controllo ({', '.join(mesi_periodo)})."
                ),
                impatto_iva=euro(abs(riga.imposta)) if riga.imposta is not None
                else IMPATTO_NON_DETERMINABILE,
                azione="verifica manuale",
                agente=agente,
            ))

        # Duplicati.
        chiave = (riga.chiave_controparte, riga.chiave_numero, riga.imponibile, riga.imposta)
        if riga.chiave_numero and riga.chiave_controparte:
            if chiave in visti:
                anomalie.append(Anomalia(
                    codice=codice("002"),
                    gravita=GRAVITA_BLOCCANTE,
                    fonte=f"{riga.fonte.testo()} (cfr. {visti[chiave].fonte.testo()})",
                    descrizione=(
                        f"{etichetta}: documento n. {riga.numero} di "
                        f"{riga.controparte or riga.piva} registrato due volte con il medesimo importo."
                    ),
                    impatto_iva=euro(abs(riga.imposta)) if riga.imposta is not None
                    else IMPATTO_NON_DETERMINABILE,
                    azione="rettifica",
                    agente=agente,
                ))
            else:
                visti[chiave] = riga

        # Numerazione per sezionale (solo numeri interi puri).
        numero_puro = re.fullmatch(r"\d+", str(riga.numero).strip()) if controlla_numerazione else None
        if numero_puro:
            numeri_per_sezionale[riga.sezionale].append((int(numero_puro.group()), riga))

    for sezionale, coppie in numeri_per_sezionale.items():
        numeri = sorted({n for n, _ in coppie})
        if len(numeri) < 3:
            continue
        mancanti = [n for n in range(numeri[0], numeri[-1] + 1) if n not in set(numeri)]
        if mancanti:
            riferimento = coppie[0][1]
            elenco = ", ".join(str(n) for n in mancanti[:20])
            if len(mancanti) > 20:
                elenco += f" (e altri {len(mancanti) - 20})"
            anomalie.append(Anomalia(
                codice=codice("001"),
                gravita=GRAVITA_VERIFICA,
                fonte=riferimento.fonte.testo(),
                descrizione=(
                    f"{etichetta}{f' sezionale {sezionale}' if sezionale else ''}: "
                    f"numerazione non continua fra {numeri[0]} e {numeri[-1]}; mancano i numeri {elenco}."
                ),
                impatto_iva=IMPATTO_NON_DETERMINABILE,
                azione="controllo gestionale",
                agente=agente,
            ))
    return anomalie


def totali_per_aliquota(righe: list[Riga]) -> list[dict]:
    """Totali per aliquota e natura, con conteggio dei documenti."""
    aggregato: dict[tuple, dict] = {}
    for riga in righe:
        chiave = (riga.aliquota, riga.natura)
        voce = aggregato.setdefault(chiave, {
            "aliquota": riga.aliquota, "natura": riga.natura,
            "imponibile": 0.0, "imposta": 0.0, "righe": 0,
        })
        voce["imponibile"] = somma([voce["imponibile"], riga.imponibile])
        voce["imposta"] = somma([voce["imposta"], riga.imposta])
        voce["righe"] += 1
    return sorted(aggregato.values(), key=lambda v: (v["aliquota"] is None, v["aliquota"] or 0, v["natura"]))


# ---------------------------------------------------------------------------
# Corrispettivi
# ---------------------------------------------------------------------------

def controlla_corrispettivi(righe: list[Riga], mesi_periodo: list[str]) -> tuple[list[Anomalia], dict]:
    """Controlli su giorni, scorporo e importi anomali."""
    anomalie: list[Anomalia] = []
    codice = Contatore("COR")
    agente = "corrispettivi-iva"
    riepilogo = {"giorni_presenti": 0, "giorni_attesi": 0, "giorni_mancanti": [],
                 "lordo": 0.0, "imponibile": 0.0, "imposta": 0.0}
    if not righe:
        return anomalie, riepilogo

    per_giorno: dict[str, list[Riga]] = defaultdict(list)
    for riga in righe:
        if riga.data:
            per_giorno[riga.data].append(riga)

    riepilogo["giorni_presenti"] = len(per_giorno)
    riepilogo["lordo"] = somma(r.totale_documento for r in righe)
    riepilogo["imponibile"] = somma(r.imponibile for r in righe)
    riepilogo["imposta"] = somma(r.imposta for r in righe)

    # Scorporo IVA dal lordo.
    for riga in righe:
        if riga.totale_documento is None or riga.aliquota is None or not riga.aliquota:
            continue
        imponibile_atteso = round(riga.totale_documento / (1 + riga.aliquota / 100), 2)
        imposta_attesa = round(riga.totale_documento - imponibile_atteso, 2)
        if riga.imposta is not None and not quasi_uguali(imposta_attesa, riga.imposta, TOLL_RIGA):
            anomalie.append(Anomalia(
                codice=codice("003"),
                gravita=GRAVITA_VERIFICA,
                fonte=riga.fonte.testo(),
                descrizione=(
                    f"Corrispettivi del {riga.data}: IVA esposta {euro(riga.imposta)} contro "
                    f"{euro(imposta_attesa)} da scorporo del lordo {euro(riga.totale_documento)} "
                    f"ad aliquota {riga.aliquota:g}%."
                ),
                impatto_iva=euro(abs(round(imposta_attesa - riga.imposta, 2))),
                azione="rettifica",
                agente=agente,
            ))

    # Giorni duplicati.
    for giorno, gruppo in per_giorno.items():
        aliquote = [r.aliquota for r in gruppo]
        if len(gruppo) > 1 and len(set(aliquote)) < len(gruppo):
            anomalie.append(Anomalia(
                codice=codice("002"),
                gravita=GRAVITA_VERIFICA,
                fonte=gruppo[0].fonte.testo(),
                descrizione=(
                    f"Corrispettivi: il giorno {giorno} compare {len(gruppo)} volte con aliquote "
                    "ripetute; verificare una possibile doppia registrazione."
                ),
                impatto_iva=euro(abs(somma(r.imposta for r in gruppo[1:]))),
                azione="verifica manuale",
                agente=agente,
            ))

    # Copertura del calendario.
    if mesi_periodo:
        attesi: list[str] = []
        for mese in mesi_periodo:
            anno, m = int(mese[:4]), int(mese[5:7])
            giorno = _dt.date(anno, m, 1)
            while giorno.month == m:
                attesi.append(giorno.isoformat())
                giorno += _dt.timedelta(days=1)
        riepilogo["giorni_attesi"] = len(attesi)
        mancanti = [g for g in attesi if g not in per_giorno]
        riepilogo["giorni_mancanti"] = mancanti
        if mancanti:
            settimane = {_dt.date.fromisoformat(g).weekday() for g in mancanti}
            giorni_settimana = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
            ricorrenza = (
                f" I giorni mancanti cadono tutti di {giorni_settimana[next(iter(settimane))]}: "
                "possibile giorno di chiusura, da confermare."
                if len(settimane) == 1 else ""
            )
            media = (
                statistics.mean([somma(r.imposta for r in g) for g in per_giorno.values()])
                if per_giorno else 0.0
            )
            anomalie.append(Anomalia(
                codice=codice("001"),
                gravita=GRAVITA_INFO if len(settimane) == 1 else GRAVITA_VERIFICA,
                fonte=righe[0].fonte.testo(),
                descrizione=(
                    f"Corrispettivi: {len(mancanti)} giorni del periodo non risultano registrati "
                    f"(primo: {mancanti[0]}, ultimo: {mancanti[-1]})." + ricorrenza
                ),
                impatto_iva=(f"stima {euro(round(media * len(mancanti), 2))} "
                             "sulla media giornaliera del periodo" if media else IMPATTO_NON_DETERMINABILE),
                azione="richiesta documento",
                agente=agente,
            ))
    return anomalie, riepilogo


# ---------------------------------------------------------------------------
# Detraibilita'
# ---------------------------------------------------------------------------

#: categoria -> (parole chiave, percentuale detraibile attesa, riferimento normativo)
CATEGORIE_DETRAIBILITA: dict[str, tuple[tuple[str, ...], float | None, str]] = {
    "autovetture": (("autovettura", "auto ", "noleggio auto", "leasing auto", "veicolo",
                     "autoveicolo", "km 0", "vettura"), 40.0, "art. 19-bis1 lett. c) DPR 633/72"),
    "carburanti": (("carburante", "benzina", "gasolio", "rifornimento", "distributore",
                    "eni ", "q8", "ip ", "tamoil"), 40.0, "art. 19-bis1 lett. d) DPR 633/72"),
    "manutenzioni auto": (("manutenzione auto", "tagliando", "pneumatici", "gommista",
                           "autofficina", "carrozzeria", "pedaggio", "telepass", "autostrad"),
                          40.0, "art. 19-bis1 lett. d) DPR 633/72"),
    "telefonia": (("telefon", "tim ", "vodafone", "wind", "fastweb", "iliad", "sim ",
                   "traffico voce"), None, "art. 19 DPR 633/72 (uso promiscuo)"),
    "alberghi": (("albergo", "hotel", "pernottamento", "b&b", "soggiorno"), 100.0,
                 "art. 19-bis1 lett. e) DPR 633/72"),
    "ristoranti": (("ristorante", "ristoraz", "pizzeria", "bar ", "catering", "trattoria",
                    "somministrazione"), 100.0, "art. 19-bis1 lett. e) DPR 633/72"),
    "omaggi": (("omaggio", "omaggi", "regalia", "cesto natalizio", "gadget"), 0.0,
               "art. 19-bis1 lett. h) DPR 633/72"),
    "rappresentanza": (("rappresentanza", "spese di rappresentanza"), 0.0,
                       "art. 19-bis1 lett. h) DPR 633/72"),
    "immobili abitativi": (("immobile abitativ", "appartamento", "locazione abitativa"), 0.0,
                           "art. 19-bis1 lett. i) DPR 633/72"),
}


def controlla_detraibilita(righe: list[Riga], prorata: float | None = None) -> list[Anomalia]:
    """Individua le posizioni a rischio di indetraibilita' totale o parziale."""
    anomalie: list[Anomalia] = []
    codice = Contatore("DET")
    agente = "detraibilita-iva"
    mappa_codici = {
        "autovetture": "001", "carburanti": "002", "manutenzioni auto": "002",
        "telefonia": "003", "alberghi": "004", "ristoranti": "004",
        "omaggi": "005", "rappresentanza": "005", "immobili abitativi": "009",
    }

    for riga in righe:
        testo = f"{riga.descrizione} {riga.controparte}".lower()
        if not testo.strip():
            continue
        for categoria, (parole, percentuale, norma) in CATEGORIE_DETRAIBILITA.items():
            if not any(p in testo for p in parole):
                continue
            if riga.imposta is None or abs(riga.imposta) <= TOLL_RIGA:
                continue
            detratta = riga.imposta - (riga.indetraibile or 0.0)
            if percentuale is None:
                descrizione = (
                    f"Acquisto riconducibile a {categoria} ({riga.controparte or 's.n.'}, "
                    f"doc. {riga.numero or 's.n.'}) con IVA di {euro(riga.imposta)}: "
                    f"verificare la quota di uso aziendale ({norma})."
                )
                impatto = f"fino a {euro(abs(detratta))} secondo l'uso effettivo"
            else:
                attesa = round(riga.imposta * percentuale / 100, 2)
                if quasi_uguali(detratta, attesa, TOLL_RIGA):
                    continue
                descrizione = (
                    f"Acquisto riconducibile a {categoria} ({riga.controparte or 's.n.'}, "
                    f"doc. {riga.numero or 's.n.'}): IVA detratta {euro(detratta)} contro "
                    f"{euro(attesa)} attesi ({percentuale:g}% ai sensi di {norma})."
                )
                impatto = euro(abs(round(detratta - attesa, 2)))
            anomalie.append(Anomalia(
                codice=codice(mappa_codici.get(categoria, "008")),
                gravita=GRAVITA_VERIFICA,
                fonte=riga.fonte.testo(),
                descrizione=descrizione,
                impatto_iva=impatto,
                azione="verifica manuale",
                agente=agente,
            ))
            break  # una sola categoria per riga: quella piu' specifica trovata

    if prorata is not None and prorata < 100:
        totale_iva = somma(r.imposta for r in righe)
        anomalie.append(Anomalia(
            codice=codice("007"),
            gravita=GRAVITA_VERIFICA,
            fonte="profilo IVA del cliente (pro-rata documentato)",
            descrizione=(
                f"Pro-rata di detrazione dichiarato al {prorata:g}%: verificare che l'IVA sugli acquisti "
                f"({euro(totale_iva)}) sia stata ridotta di conseguenza."
            ),
            impatto_iva=euro(round(totale_iva * (100 - prorata) / 100, 2)),
            azione="verifica manuale",
            agente=agente,
        ))
    return anomalie


# ---------------------------------------------------------------------------
# Reverse charge / split payment / estero
# ---------------------------------------------------------------------------

def controlla_reverse_split_estero(
    righe_passive: list[Riga], righe_vendite: list[Riga], righe_acquisti: list[Riga]
) -> tuple[list[Anomalia], dict]:
    """Verifica la doppia annotazione delle operazioni a inversione contabile."""
    anomalie: list[Anomalia] = []
    codice = Contatore("RCS")
    agente = "reverse-split-estero-iva"
    riepilogo = {"operazioni": 0, "iva_debito": 0.0, "iva_credito": 0.0, "split_payment": 0.0}

    reverse = [r for r in righe_passive if r.reverse_charge]
    riepilogo["operazioni"] = len({(r.chiave_controparte, r.chiave_numero) for r in reverse})

    def presente_in(righe: list[Riga], documento: Riga) -> Riga | None:
        """Cerca l'annotazione corrispondente in un registro.

        L'integrazione di una fattura in inversione contabile riceve un proprio
        numero di protocollo, diverso da quello del documento del fornitore: il
        match sul numero e' quindi solo il primo criterio, seguito dal match su
        controparte e imponibile.
        """
        candidate = [r for r in righe if r.chiave_controparte and
                     r.chiave_controparte == documento.chiave_controparte]
        for candidata in candidate:
            if candidata.chiave_numero and candidata.chiave_numero == documento.chiave_numero:
                return candidata
        for candidata in candidate:
            if quasi_uguali(candidata.imponibile, documento.imponibile, TOLL_DOCUMENTO):
                return candidata
        return None

    visti: set[tuple] = set()
    for documento in reverse:
        chiave = (documento.chiave_controparte, documento.chiave_numero)
        if chiave in visti:
            continue
        visti.add(chiave)
        in_vendite = presente_in(righe_vendite, documento)
        in_acquisti = presente_in(righe_acquisti, documento)
        # Nell'XML in inversione contabile l'imposta e' zero (natura N6): l'importo
        # rilevante e' quello integrato nei registri, o in mancanza il calcolo
        # sull'imponibile all'aliquota ordinaria del documento.
        imposta = documento.imposta or 0.0
        if abs(imposta) <= TOLL_RIGA:
            for candidata in (in_acquisti, in_vendite):
                if candidata is not None and candidata.imposta:
                    imposta = candidata.imposta
                    break

        if in_vendite is None and in_acquisti is not None:
            anomalie.append(Anomalia(
                codice=codice("001"),
                gravita=GRAVITA_BLOCCANTE,
                fonte=documento.fonte.testo(),
                descrizione=(
                    f"Operazione in inversione contabile (doc. {documento.numero}, "
                    f"{documento.controparte or documento.piva}, tipo {documento.tipo_documento or 'n.d.'}) "
                    "annotata nel registro acquisti ma non nel registro vendite: IVA a debito omessa."
                ),
                impatto_iva=euro(abs(imposta)),
                azione="rettifica",
                agente=agente,
            ))
        elif in_acquisti is None and in_vendite is not None:
            anomalie.append(Anomalia(
                codice=codice("002"),
                gravita=GRAVITA_VERIFICA,
                fonte=documento.fonte.testo(),
                descrizione=(
                    f"Operazione in inversione contabile (doc. {documento.numero}, "
                    f"{documento.controparte or documento.piva}) annotata nel registro vendite "
                    "ma non nel registro acquisti: detrazione non esercitata."
                ),
                impatto_iva=euro(abs(imposta)),
                azione="verifica manuale",
                agente=agente,
            ))
        elif in_vendite is None and in_acquisti is None:
            anomalie.append(Anomalia(
                codice=codice("010"),
                gravita=GRAVITA_BLOCCANTE,
                fonte=documento.fonte.testo(),
                descrizione=(
                    f"Documento {documento.tipo_documento or 'in inversione contabile'} n. "
                    f"{documento.numero} presente fra gli XML e assente da entrambi i registri IVA."
                ),
                impatto_iva=euro(abs(imposta)),
                azione="rettifica",
                agente=agente,
            ))
        else:
            if not quasi_uguali(in_vendite.imposta, in_acquisti.imposta, TOLL_RIGA):
                anomalie.append(Anomalia(
                    codice=codice("003"),
                    gravita=GRAVITA_BLOCCANTE,
                    fonte=f"{in_vendite.fonte.testo()}; {in_acquisti.fonte.testo()}",
                    descrizione=(
                        f"Doppia annotazione con importi divergenti sul doc. {documento.numero}: "
                        f"vendite {euro(in_vendite.imposta)} contro acquisti {euro(in_acquisti.imposta)}."
                    ),
                    impatto_iva=euro(abs(round((in_vendite.imposta or 0) - (in_acquisti.imposta or 0), 2))),
                    azione="rettifica",
                    agente=agente,
                ))
            elif in_vendite.data and in_acquisti.data and in_vendite.data[:7] != in_acquisti.data[:7]:
                anomalie.append(Anomalia(
                    codice=codice("004"),
                    gravita=GRAVITA_VERIFICA,
                    fonte=f"{in_vendite.fonte.testo()}; {in_acquisti.fonte.testo()}",
                    descrizione=(
                        f"Doppia annotazione in periodi diversi sul doc. {documento.numero}: "
                        f"vendite {in_vendite.data}, acquisti {in_acquisti.data}."
                    ),
                    impatto_iva=euro(abs(imposta)),
                    azione="verifica manuale",
                    agente=agente,
                ))
            riepilogo["iva_debito"] = somma([riepilogo["iva_debito"], in_vendite.imposta])
            riepilogo["iva_credito"] = somma([riepilogo["iva_credito"], in_acquisti.imposta])

    # Natura N6 su operazioni attive: verifica di plausibilita'.
    for riga in righe_vendite:
        if riga.natura.startswith("N6") and riga.imposta and abs(riga.imposta) > TOLL_RIGA:
            anomalie.append(Anomalia(
                codice=codice("005"),
                gravita=GRAVITA_VERIFICA,
                fonte=riga.fonte.testo(),
                descrizione=(
                    f"Vendita con natura {riga.natura} (inversione contabile) e imposta "
                    f"{euro(riga.imposta)} esposta: le due indicazioni sono incompatibili."
                ),
                impatto_iva=euro(abs(riga.imposta)),
                azione="verifica manuale",
                agente=agente,
            ))

    split = [r for r in righe_vendite if r.split_payment or r.esigibilita == "S"]
    riepilogo["split_payment"] = somma(r.imposta for r in split)
    return anomalie, riepilogo


# ---------------------------------------------------------------------------
# Prima nota / F24
# ---------------------------------------------------------------------------

CODICI_TRIBUTO_MENSILI = {f"60{n:02d}" for n in range(1, 13)}
CODICI_TRIBUTO_TRIMESTRALI = {"6031", "6032", "6033", "6034"}
CODICI_TRIBUTO_ALTRI = {"6013", "6035", "6099"}


def controlla_prima_nota(
    righe_prima_nota: list[Riga],
    voci_f24: list[dict],
    debito_precedente: float | None,
    trimestrale: bool,
) -> list[Anomalia]:
    """Riconciliazione fra versamenti, prima nota e liquidazione."""
    anomalie: list[Anomalia] = []
    codice = Contatore("PN")
    agente = "prima-nota-iva"

    if debito_precedente is not None and debito_precedente > 0 and not voci_f24:
        anomalie.append(Anomalia(
            codice=codice("001"),
            gravita=GRAVITA_BLOCCANTE,
            fonte="cartella 06_crediti_precedenti_f24_lipe",
            descrizione=(
                f"Debito IVA del periodo precedente pari a {euro(debito_precedente)}: "
                "non risulta alcun F24 quietanzato fra i documenti forniti."
            ),
            impatto_iva=euro(debito_precedente),
            azione="richiesta documento",
            agente=agente,
        ))

    for voce in voci_f24:
        tributo = str(voce.get("codice_tributo", "")).strip()
        importo = voce.get("importo")
        fonte = voce.get("fonte", "F24")
        if tributo and tributo not in (CODICI_TRIBUTO_MENSILI | CODICI_TRIBUTO_TRIMESTRALI | CODICI_TRIBUTO_ALTRI):
            anomalie.append(Anomalia(
                codice=codice("003"),
                gravita=GRAVITA_VERIFICA,
                fonte=fonte,
                descrizione=f"F24 con codice tributo {tributo} non riconducibile all'IVA periodica.",
                impatto_iva=euro(importo) if importo is not None else IMPATTO_NON_DETERMINABILE,
                azione="verifica manuale",
                agente=agente,
            ))
        elif tributo and trimestrale and tributo in CODICI_TRIBUTO_MENSILI:
            anomalie.append(Anomalia(
                codice=codice("003"),
                gravita=GRAVITA_VERIFICA,
                fonte=fonte,
                descrizione=(
                    f"F24 con codice tributo mensile {tributo} per un contribuente trimestrale."
                ),
                impatto_iva=euro(importo) if importo is not None else IMPATTO_NON_DETERMINABILE,
                azione="verifica manuale",
                agente=agente,
            ))
        if debito_precedente is not None and importo is not None and debito_precedente > 0:
            if abs(importo - debito_precedente) > TOLL_DOCUMENTO:
                anomalie.append(Anomalia(
                    codice=codice("002"),
                    gravita=GRAVITA_VERIFICA,
                    fonte=fonte,
                    descrizione=(
                        f"Versamento F24 di {euro(importo)} a fronte di un debito liquidato di "
                        f"{euro(debito_precedente)}."
                    ),
                    impatto_iva=euro(abs(round(importo - debito_precedente, 2))),
                    azione="verifica manuale",
                    agente=agente,
                ))

    for riga in righe_prima_nota:
        testo = f"{riga.descrizione} {riga.controparte}".lower()
        if "erario" in testo and "iva" in testo and riga.totale_documento is None and riga.imposta is None:
            anomalie.append(Anomalia(
                codice=codice("007"),
                gravita=GRAVITA_VERIFICA,
                fonte=riga.fonte.testo(),
                descrizione="Movimento su conto Erario c/IVA privo di importo leggibile.",
                impatto_iva=IMPATTO_NON_DETERMINABILE,
                azione="verifica manuale",
                agente=agente,
            ))
    return anomalie


# ---------------------------------------------------------------------------
# Quality review
# ---------------------------------------------------------------------------

def quality_review(anomalie: list[Anomalia]) -> tuple[list[Anomalia], list[dict]]:
    """Applica i test del Quality Reviewer e restituisce ``(confermate, correzioni)``."""
    confermate: list[Anomalia] = []
    correzioni: list[dict] = []
    viste: dict[tuple, Anomalia] = {}
    per_fatto: dict[tuple, Anomalia] = {}

    for anomalia in anomalie:
        # Test 1 - tracciabilita'.
        if not anomalia.fonte.strip() or anomalia.fonte.strip() == "fonte non indicata":
            correzioni.append({
                "codice": "QR-001", "rilievo": anomalia.codice, "esito": "scartato",
                "motivo": "rilievo privo di fonte verificabile",
            })
            continue

        # Test 2 - non duplicazione: stesso fatto, stessa fonte, stessa descrizione.
        chiave = (anomalia.fonte.strip().lower(), anomalia.descrizione.strip().lower()[:120])
        if chiave in viste:
            correzioni.append({
                "codice": "QR-002", "rilievo": anomalia.codice, "esito": "fuso",
                "motivo": f"duplicato di {viste[chiave].codice} sulla medesima fonte",
            })
            continue
        viste[chiave] = anomalia

        # Test 2 (seconda applicazione): stesso fatto rilevato su fonti diverse.
        # Si conserva il primo rilievo, arricchito con l'ulteriore riscontro.
        famiglia = anomalia.codice.rsplit(".", 1)[0]
        chiave_fatto = (famiglia, anomalia.descrizione.strip().lower())
        if chiave_fatto in per_fatto:
            originale = per_fatto[chiave_fatto]
            originale.fonte = f"{originale.fonte}; cfr. {anomalia.fonte}"
            correzioni.append({
                "codice": "QR-002", "rilievo": anomalia.codice, "esito": "fuso",
                "motivo": f"stesso fatto gia' rilevato in {originale.codice} su altra fonte",
            })
            continue
        per_fatto[chiave_fatto] = anomalia

        # Test 4 - fondatezza dell'impatto: un impatto pari a zero non e' un impatto.
        if anomalia.impatto_iva.strip() in {"0,00", "0.00", "0", ""}:
            correzioni.append({
                "codice": "QR-004", "rilievo": anomalia.codice, "esito": "impatto riclassificato",
                "motivo": "impatto dichiarato pari a zero, sostituito con 'non determinabile'",
            })
            anomalia.impatto_iva = IMPATTO_NON_DETERMINABILE

        confermate.append(anomalia)
    return confermate, correzioni
