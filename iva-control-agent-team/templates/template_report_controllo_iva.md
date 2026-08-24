# Controllo IVA — {{CLIENTE}} — {{PERIODO}}

<!--
Template del report finale prodotto dall'agente report-writer-iva.
I segnaposto {{...}} sono sostituiti da src/export_outputs.py o dall'agente.
Le sezioni da 1 a 7 sono obbligatorie e non vanno rinominate né riordinate.
-->

## 1. Esito sintetico

Esito: **{{ESITO}}**

{{SINTESI}}

## 2. Fonti analizzate

{{FONTI}}

Fonti mancanti: {{FONTI_MANCANTI}}

## 3. Quadratura liquidazione

| Voce | Da fonti/registri | Da bozza liquidazione | Differenza |
|---|---:|---:|---:|
| IVA vendite | {{IVA_VENDITE_FONTI}} | {{IVA_VENDITE_BOZZA}} | {{IVA_VENDITE_DIFF}} |
| IVA corrispettivi | {{IVA_CORRISPETTIVI_FONTI}} | {{IVA_CORRISPETTIVI_BOZZA}} | {{IVA_CORRISPETTIVI_DIFF}} |
| IVA acquisti detraibile | {{IVA_ACQUISTI_FONTI}} | {{IVA_ACQUISTI_BOZZA}} | {{IVA_ACQUISTI_DIFF}} |
| Credito precedente | {{CREDITO_PREC_FONTI}} | {{CREDITO_PREC_BOZZA}} | {{CREDITO_PREC_DIFF}} |
| Debito/Credito periodo | {{SALDO_FONTI}} | {{SALDO_BOZZA}} | {{SALDO_DIFF}} |

{{NOTA_QUADRATURA}}

## 4. Anomalie principali

| Codice | Gravità | Fonte | Descrizione | Impatto IVA | Azione |
|---|---|---|---|---:|---|
{{TABELLA_ANOMALIE}}

{{NOTA_ANOMALIE}}

## 5. Altri rilievi informativi

{{RILIEVI_INFORMATIVI}}

## 6. Documenti o chiarimenti da richiedere

{{RICHIESTE}}

## 7. Conclusione operativa

{{CONCLUSIONE}}

---

*Controllo eseguito il {{DATA_CONTROLLO}} con `iva-control-agent-team`. Dettaglio tecnico in
`anomalie_iva.xlsx` e `dataset_normalizzato.xlsx`; audit trail delle fonti in `fonti_utilizzate.json`.
Il presente report non sostituisce il giudizio professionale del commercialista.*
