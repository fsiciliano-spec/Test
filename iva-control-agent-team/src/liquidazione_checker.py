"""Ricalcolo della liquidazione IVA del periodo e confronto con la bozza.

Se manca una fonte necessaria il ricalcolo e' dichiarato parziale e l'esito
numerico e' ``DA SOSPENDERE``: nessuna voce viene stimata per colmare un vuoto
documentale.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from normalize import (
    Anomalia,
    DIFF_RILEVANTE_EUR,
    DIFF_RILEVANTE_PERC,
    ESITO_OK,
    ESITO_SOSPENDERE,
    ESITO_VERIFICA,
    GRAVITA_BLOCCANTE,
    GRAVITA_INFO,
    GRAVITA_VERIFICA,
    IMPATTO_NON_DETERMINABILE,
    Riga,
    SOGLIA_MINIMA_VERSAMENTO,
    TOLL_DOCUMENTO,
    euro,
    somma,
)

AGENTE = "liquidazione-checker-iva"

VOCI_CONFRONTO = [
    ("iva_vendite", "IVA vendite"),
    ("iva_corrispettivi", "IVA corrispettivi"),
    ("iva_acquisti", "IVA acquisti detraibile"),
    ("credito_precedente", "Credito precedente"),
    ("saldo", "Debito/Credito periodo"),
]


@dataclass
class Liquidazione:
    """Esito del ricalcolo, voce per voce, con l'indicazione delle fonti usate."""

    iva_vendite: float | None = None
    iva_corrispettivi: float | None = None
    iva_reverse_debito: float = 0.0
    iva_split_esclusa: float = 0.0
    iva_acquisti: float | None = None
    iva_reverse_credito: float = 0.0
    iva_indetraibile: float = 0.0
    credito_precedente: float | None = None
    debito_precedente: float = 0.0
    acconto: float = 0.0
    compensazioni: float = 0.0
    interessi: float = 0.0
    trimestrale: bool = False
    ultimo_periodo: bool = False
    fonti: dict[str, str] = field(default_factory=dict)
    voci_non_ricalcolabili: list[str] = field(default_factory=list)

    @property
    def iva_esigibile(self) -> float | None:
        if self.iva_vendite is None and self.iva_corrispettivi is None:
            return None
        return round(somma([self.iva_vendite, self.iva_corrispettivi, self.iva_reverse_debito]), 2)

    @property
    def iva_detraibile(self) -> float | None:
        if self.iva_acquisti is None:
            return None
        return round(self.iva_acquisti + self.iva_reverse_credito - self.iva_indetraibile, 2)

    @property
    def saldo_periodo(self) -> float | None:
        esigibile, detraibile = self.iva_esigibile, self.iva_detraibile
        if esigibile is None or detraibile is None:
            return None
        return round(esigibile - detraibile, 2)

    @property
    def risultato(self) -> float | None:
        saldo = self.saldo_periodo
        if saldo is None:
            return None
        return round(
            saldo - (self.credito_precedente or 0.0) + self.debito_precedente
            - self.acconto - self.compensazioni,
            2,
        )

    @property
    def importo_da_versare(self) -> float | None:
        """Importo da versare arrotondato all'unita' di euro, interessi inclusi.

        Restituisce 0.0 quando il risultato e' a credito o non supera la soglia
        minima di versamento (25,82 euro), che si riporta al periodo successivo.
        """
        risultato = self.risultato
        if risultato is None:
            return None
        if risultato <= 0:
            return 0.0
        totale = risultato + self.interessi
        if totale <= SOGLIA_MINIMA_VERSAMENTO:
            return 0.0
        return float(round(totale))

    @property
    def completa(self) -> bool:
        return not self.voci_non_ricalcolabili

    def calcola_interessi(self) -> None:
        """Interessi dell'1% dovuti dai trimestrali, non sull'ultimo trimestre."""
        risultato = self.risultato
        if self.trimestrale and not self.ultimo_periodo and risultato and risultato > 0:
            self.interessi = round(risultato * 0.01, 2)
        else:
            self.interessi = 0.0

    def as_dict(self) -> dict:
        return {
            "IVA vendite": self.iva_vendite,
            "IVA corrispettivi": self.iva_corrispettivi,
            "IVA reverse charge a debito": self.iva_reverse_debito,
            "IVA in split payment (esclusa)": self.iva_split_esclusa,
            "IVA esigibile": self.iva_esigibile,
            "IVA acquisti": self.iva_acquisti,
            "IVA reverse charge a credito": self.iva_reverse_credito,
            "IVA indetraibile (rilevata)": self.iva_indetraibile,
            "IVA detraibile": self.iva_detraibile,
            "Saldo del periodo": self.saldo_periodo,
            "Credito periodo precedente": self.credito_precedente,
            "Debito periodo precedente": self.debito_precedente,
            "Acconto IVA": self.acconto,
            "Compensazioni": self.compensazioni,
            "Risultato": self.risultato,
            "Interessi 1%": self.interessi,
            "Importo da versare": self.importo_da_versare,
        }


def ricalcola(
    righe_vendite: list[Riga],
    righe_acquisti: list[Riga],
    righe_corrispettivi: list[Riga],
    *,
    reverse_debito: float = 0.0,
    reverse_credito: float = 0.0,
    iva_indetraibile: float = 0.0,
    credito_precedente: float | None = None,
    debito_precedente: float = 0.0,
    acconto: float = 0.0,
    compensazioni: float = 0.0,
    trimestrale: bool = False,
    ultimo_periodo: bool = False,
    fonti: dict[str, str] | None = None,
) -> Liquidazione:
    """Ricalcola la liquidazione dai dataset verificati."""
    liquidazione = Liquidazione(
        iva_reverse_debito=round(reverse_debito, 2),
        iva_reverse_credito=round(reverse_credito, 2),
        iva_indetraibile=round(iva_indetraibile, 2),
        credito_precedente=credito_precedente,
        debito_precedente=round(debito_precedente, 2),
        acconto=round(acconto, 2),
        compensazioni=round(compensazioni, 2),
        trimestrale=trimestrale,
        ultimo_periodo=ultimo_periodo,
        fonti=dict(fonti or {}),
    )

    if righe_vendite:
        # Lo split payment e' escluso dall'IVA esigibile del cedente.
        split = somma(r.imposta for r in righe_vendite if r.split_payment)
        liquidazione.iva_split_esclusa = split
        liquidazione.iva_vendite = round(
            somma(r.imposta for r in righe_vendite if not r.split_payment), 2
        )
    else:
        liquidazione.voci_non_ricalcolabili.append("IVA vendite")

    if righe_corrispettivi:
        liquidazione.iva_corrispettivi = somma(r.imposta for r in righe_corrispettivi)
    # L'assenza di corrispettivi non e' di per se' una lacuna: molti soggetti non ne hanno.

    if righe_acquisti:
        liquidazione.iva_acquisti = somma(r.imposta for r in righe_acquisti)
    else:
        liquidazione.voci_non_ricalcolabili.append("IVA acquisti")

    if credito_precedente is None:
        liquidazione.voci_non_ricalcolabili.append("Credito periodo precedente")

    liquidazione.calcola_interessi()
    return liquidazione


def confronta_con_bozza(
    liquidazione: Liquidazione, bozza: dict[str, dict]
) -> tuple[list[dict], list[Anomalia], str]:
    """Confronta il ricalcolo con la bozza del gestionale.

    Restituisce ``(tabella_confronto, anomalie, esito_numerico)``.
    """
    anomalie: list[Anomalia] = []
    tabella: list[dict] = []
    progressivo = {"001": 0, "002": 0, "003": 0, "006": 0, "007": 0, "008": 0}

    def codice(suffisso: str) -> str:
        progressivo[suffisso] = progressivo.get(suffisso, 0) + 1
        return f"LIQ-{suffisso}.{progressivo[suffisso]:02d}"

    valori_ricalcolati = {
        "iva_vendite": liquidazione.iva_vendite,
        "iva_corrispettivi": liquidazione.iva_corrispettivi,
        "iva_acquisti": liquidazione.iva_detraibile,
        "credito_precedente": liquidazione.credito_precedente,
        "saldo": liquidazione.risultato,
    }
    codici_anomalia = {
        "iva_vendite": "001", "iva_corrispettivi": "001",
        "iva_acquisti": "002", "credito_precedente": "003", "saldo": "006",
    }

    differenza_massima = 0.0
    esigibile = liquidazione.iva_esigibile or 0.0

    for chiave, etichetta in VOCI_CONFRONTO:
        da_fonti = valori_ricalcolati.get(chiave)
        voce_bozza = bozza.get(chiave)
        da_bozza = voce_bozza["importo"] if voce_bozza else None
        differenza = (
            round(da_fonti - da_bozza, 2)
            if da_fonti is not None and da_bozza is not None
            else None
        )
        tabella.append({
            "voce": etichetta,
            "da_fonti": da_fonti,
            "da_bozza": da_bozza,
            "differenza": differenza,
            "fonte_bozza": (f"pag. {voce_bozza['pagina']}: {voce_bozza['riga'][:80]}"
                            if voce_bozza else "voce non individuata nella bozza"),
        })

        if differenza is None:
            if da_bozza is None and da_fonti is not None:
                anomalie.append(
                    Anomalia(
                        codice=codice("007"),
                        gravita=GRAVITA_VERIFICA,
                        fonte=liquidazione.fonti.get("bozza", "bozza di liquidazione"),
                        descrizione=(
                            f"La voce «{etichetta}» e' stata ricalcolata dalle fonti "
                            f"({euro(da_fonti)}) ma non e' stata individuata nella bozza di liquidazione."
                        ),
                        impatto_iva=IMPATTO_NON_DETERMINABILE,
                        azione="verifica manuale",
                        agente=AGENTE,
                    )
                )
            continue

        differenza_massima = max(differenza_massima, abs(differenza))
        if abs(differenza) <= TOLL_DOCUMENTO:
            continue

        rilevante = abs(differenza) > DIFF_RILEVANTE_EUR or (
            esigibile > 0 and abs(differenza) > esigibile * DIFF_RILEVANTE_PERC / 100
        )
        anomalie.append(
            Anomalia(
                codice=codice(codici_anomalia[chiave]),
                gravita=GRAVITA_BLOCCANTE if rilevante else GRAVITA_VERIFICA,
                fonte=(f"{liquidazione.fonti.get('bozza', 'bozza di liquidazione')}; "
                       f"{liquidazione.fonti.get(chiave, 'dataset normalizzato')}"),
                descrizione=(
                    f"«{etichetta}»: ricalcolo dalle fonti {euro(da_fonti)} contro bozza "
                    f"{euro(da_bozza)} (differenza {euro(differenza)})."
                ),
                impatto_iva=euro(abs(differenza)),
                azione="rettifica" if rilevante else "verifica manuale",
                agente=AGENTE,
            )
        )

    if liquidazione.voci_non_ricalcolabili:
        anomalie.append(
            Anomalia(
                codice="LIQ-008.01",
                gravita=GRAVITA_BLOCCANTE,
                fonte="checklist documentale del controllo",
                descrizione=(
                    "Ricalcolo della liquidazione parziale: non e' stato possibile determinare "
                    + ", ".join(liquidazione.voci_non_ricalcolabili)
                    + " per assenza delle fonti corrispondenti."
                ),
                impatto_iva=IMPATTO_NON_DETERMINABILE,
                azione="richiesta documento",
                agente=AGENTE,
            )
        )

    if liquidazione.trimestrale and not liquidazione.ultimo_periodo:
        voce_interessi = bozza.get("interessi")
        if liquidazione.interessi > 0 and voce_interessi is None:
            anomalie.append(
                Anomalia(
                    codice="LIQ-004.01",
                    gravita=GRAVITA_VERIFICA,
                    fonte=liquidazione.fonti.get("bozza", "bozza di liquidazione"),
                    descrizione=(
                        f"Contribuente trimestrale con debito di periodo: attesi interessi dell'1% pari a "
                        f"{euro(liquidazione.interessi)}, non individuati nella bozza."
                    ),
                    impatto_iva=euro(liquidazione.interessi),
                    azione="rettifica",
                    agente=AGENTE,
                )
            )
        elif voce_interessi and abs(voce_interessi["importo"] - liquidazione.interessi) > TOLL_DOCUMENTO:
            anomalie.append(
                Anomalia(
                    codice="LIQ-004.02",
                    gravita=GRAVITA_VERIFICA,
                    fonte=liquidazione.fonti.get("bozza", "bozza di liquidazione"),
                    descrizione=(
                        f"Interessi trimestrali: ricalcolati {euro(liquidazione.interessi)} contro "
                        f"{euro(voce_interessi['importo'])} in bozza."
                    ),
                    impatto_iva=euro(abs(voce_interessi["importo"] - liquidazione.interessi)),
                    azione="rettifica",
                    agente=AGENTE,
                )
            )

    if liquidazione.iva_split_esclusa > 0:
        anomalie.append(
            Anomalia(
                codice="LIQ-009.01",
                gravita=GRAVITA_INFO,
                fonte=liquidazione.fonti.get("iva_vendite", "dataset vendite"),
                descrizione=(
                    f"IVA in regime di scissione dei pagamenti pari a {euro(liquidazione.iva_split_esclusa)} "
                    "esclusa dall'IVA esigibile del periodo, come previsto dall'art. 17-ter DPR 633/72."
                ),
                impatto_iva=euro(liquidazione.iva_split_esclusa),
                azione="controllo gestionale",
                agente=AGENTE,
            )
        )

    # Esito numerico.
    if not liquidazione.completa or not bozza:
        esito = ESITO_SOSPENDERE
    elif any(a.gravita == GRAVITA_BLOCCANTE for a in anomalie):
        esito = ESITO_SOSPENDERE
    elif differenza_massima > TOLL_DOCUMENTO:
        esito = ESITO_VERIFICA
    else:
        esito = ESITO_OK
    return tabella, anomalie, esito
