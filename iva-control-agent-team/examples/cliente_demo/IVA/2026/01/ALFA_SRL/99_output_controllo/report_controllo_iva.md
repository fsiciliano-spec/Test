# Controllo IVA — ALFA_SRL — 01 2026 (2026-01 — 2026-01)


## 1. Esito sintetico

Esito: **DA SOSPENDERE**

- Controlli effettuati: quadratura XML/registri su 10 documenti (copertura 40.0%); controllo numerazione, date e quadratura di riga dei registri; corrispettivi su 21 giorni registrati; verifica doppia annotazione su 1 operazioni in inversione contabile; ricalcolo della liquidazione e confronto con la bozza.
- Nessuna differenza fra ricalcolo e bozza oltre la tolleranza di arrotondamento.
- Anomalie: 1 bloccanti, 7 da verificare, 2 informative.
- Conclusione: controllo **sospeso** per la presenza di rilievi bloccanti da rettificare prima della liquidazione.

## 2. Fonti analizzate

- **XML fatture**: presente, 5 file
- **Registri IVA**: presente, 2 file
- **Corrispettivi**: presente, 1 file
- **Prima nota**: presente, 1 file
- **Bozza liquidazione**: presente, 1 file
- **F24 / LIPE / crediti precedenti**: presente, 1 file
- Righe normalizzate: corrispettivi 21, crediti_f24_lipe 1, prima_nota 2, registro_acquisti 5, registro_vendite 4, xml_attiva 3, xml_passiva 2
- P.IVA del cliente utilizzata per la ripartizione attive/passive: 01234567890 (profilo_cliente.json)

Fonti mancanti: nessuna

## 3. Quadratura liquidazione

| Voce | Da fonti/registri | Da bozza liquidazione | Differenza |
|---|---:|---:|---:|
| IVA vendite | 4.400,00 | 4.400,00 | 0,00 |
| IVA corrispettivi | 3.817,00 | 3.817,00 | 0,00 |
| IVA acquisti detraibile | 5.880,00 | 5.880,00 | 0,00 |
| Credito precedente | 0,00 | 0,00 | 0,00 |
| Debito/Credito periodo | 2.337,00 | 2.337,00 | 0,00 |



## 4. Anomalie principali

| Codice | Gravità | Fonte | Descrizione | Impatto IVA | Azione |
|---|---|---|---|---:|---|
| XML-REG-001.01 | Bloccante | IT01234567890_00003.xml — riga 1 — doc. 3/2026 | Fattura attiva n. 3/2026 del 2026-01-29 (DELTA S.R.L.) presente fra gli XML e non rintracciata nel registro IVA. | 440,00 | rettifica |
| DET-001.01 | Da verificare | registro_acquisti_2026_01.csv — riga 2 — doc. 120; cfr. IT55566677788_00120.xml — riga 1 — doc. 120 | Acquisto riconducibile a autovetture (AUTO CENTRO S.R.L., doc. 120): IVA detratta 4.400,00 contro 1.760,00 attesi (40% ai sensi di art. 19-bis1 lett. c) DPR 633/72). | 2.640,00 | verifica manuale |
| COR-001.01 | Da verificare | corrispettivi_2026_01.csv — riga 2 | Corrispettivi: 10 giorni del periodo non risultano registrati (primo: 2026-01-01, ultimo: 2026-01-26). | stima 1.817,62 sulla media giornaliera del periodo | richiesta documento |
| XML-REG-002.02 | Da verificare | registro_acquisti_2026_01.csv — riga 4 — doc. 77 | Registrazione n. 77 del 2026-01-08 (UFFICIO FORNITURE S.P.A.) priva di XML corrispondente. Verificare se si tratta di documento legittimamente non elettronico (fattura estera, bolletta doganale, autofattura) prima di qualificarla come anomalia. | 264,00 | richiesta documento |
| XML-REG-002.04 | Da verificare | registro_acquisti_2026_01.csv — riga 6 — doc. 900 | Registrazione n. 900 del 2026-01-25 (TIM S.P.A.) priva di XML corrispondente. Verificare se si tratta di documento legittimamente non elettronico (fattura estera, bolletta doganale, autofattura) prima di qualificarla come anomalia. | 66,00 | richiesta documento |
| DET-003.01 | Da verificare | registro_acquisti_2026_01.csv — riga 6 — doc. 900 | Acquisto riconducibile a telefonia (TIM S.P.A., doc. 900) con IVA di 66,00: verificare la quota di uso aziendale (art. 19 DPR 633/72 (uso promiscuo)). | fino a 66,00 secondo l'uso effettivo | verifica manuale |
| XML-REG-002.03 | Da verificare | registro_acquisti_2026_01.csv — riga 5 — doc. 312 | Registrazione n. 312 del 2026-01-22 (RISTORANTE DA MARIO) priva di XML corrispondente. Verificare se si tratta di documento legittimamente non elettronico (fattura estera, bolletta doganale, autofattura) prima di qualificarla come anomalia. | 50,00 | richiesta documento |
| XML-REG-002.01 | Da verificare | registro_vendite_2026_01.csv — riga 5 — doc. 5/2026 | Registrazione n. 5/2026 del 2026-01-31 (Corrispettivi del mese - riepilogo) priva di XML corrispondente. Verificare se si tratta di documento legittimamente non elettronico (fattura estera, bolletta doganale, autofattura) prima di qualificarla come anomalia. | non determinabile | richiesta documento |



## 5. Altri rilievi informativi

- `XML-REG-009.01` Registrazione n. 4/2026 riconducibile all'integrazione della fattura in inversione contabile n. 45 di EDIL SUB S.R.L.: l'assenza di un XML proprio e' fisiologica. *(fonte: registro_vendite_2026_01.csv — riga 4 — doc. 4/2026; IT99988877766_00045.xml — riga 1 — doc. 45)*
- `XML-REG-009.02` Documento n. 45 in inversione contabile: imposta assente nell'XML e integrata nel registro per 1.100,00. Trattamento coerente; la doppia annotazione e' verificata dall'agente reverse-split-estero-iva. *(fonte: IT99988877766_00045.xml — riga 1 — doc. 45; registro_acquisti_2026_01.csv — riga 3 — doc. 45)*

## 6. Documenti o chiarimenti da richiedere

1. Registrazione n. 5/2026 del 2026-01-31 (Corrispettivi del mese - riepilogo) priva di XML corrispondente. Verificare se si tratta di documento legittimamente non elettronico (fattura estera, bolletta doganale, autofattura) prima di qualificarla come anomalia. *(rif. XML-REG-002.01, fonte: registro_vendite_2026_01.csv — riga 5 — doc. 5/2026)*
2. Registrazione n. 77 del 2026-01-08 (UFFICIO FORNITURE S.P.A.) priva di XML corrispondente. Verificare se si tratta di documento legittimamente non elettronico (fattura estera, bolletta doganale, autofattura) prima di qualificarla come anomalia. *(rif. XML-REG-002.02, fonte: registro_acquisti_2026_01.csv — riga 4 — doc. 77)*
3. Registrazione n. 312 del 2026-01-22 (RISTORANTE DA MARIO) priva di XML corrispondente. Verificare se si tratta di documento legittimamente non elettronico (fattura estera, bolletta doganale, autofattura) prima di qualificarla come anomalia. *(rif. XML-REG-002.03, fonte: registro_acquisti_2026_01.csv — riga 5 — doc. 312)*
4. Registrazione n. 900 del 2026-01-25 (TIM S.P.A.) priva di XML corrispondente. Verificare se si tratta di documento legittimamente non elettronico (fattura estera, bolletta doganale, autofattura) prima di qualificarla come anomalia. *(rif. XML-REG-002.04, fonte: registro_acquisti_2026_01.csv — riga 6 — doc. 900)*
5. Corrispettivi: 10 giorni del periodo non risultano registrati (primo: 2026-01-01, ultimo: 2026-01-26). *(rif. COR-001.01, fonte: corrispettivi_2026_01.csv — riga 2)*

## 7. Conclusione operativa

Esito: **DA SOSPENDERE**.

1. Rettificare il rilievo bloccante prima dell'invio della liquidazione.
2. Sottoporre a verifica professionale i 7 rilievi da verificare, in particolare per gli importi con impatto IVA quantificato.
3. Esito da confermare a cura del professionista incaricato.

---

*Controllo eseguito il 2026-08-24 con `iva-control-agent-team`. Dettaglio tecnico in
`anomalie_iva.xlsx` e `dataset_normalizzato.xlsx`; audit trail delle fonti in `fonti_utilizzate.json`.
Il presente report non sostituisce il giudizio professionale del commercialista.*
