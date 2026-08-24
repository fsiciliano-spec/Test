# iva-control-agent-team

Team di agenti per il **controllo IVA periodico** (mensile o trimestrale) dei clienti di
uno studio professionale italiano, utilizzabile da Claude Code e affiancato da una
libreria Python che esegue in modo deterministico i controlli quantitativi.

Il sistema legge una cartella documentale, normalizza fonti eterogenee (XML/P7M, PDF,
Excel, CSV), confronta fatture, registri, corrispettivi, prima nota e bozza di
liquidazione, ricalcola la liquidazione e produce un esito professionale
**OK / DA VERIFICARE / DA SOSPENDERE** con un report per il commercialista.

> Principio non negoziabile: **nessun dato inventato**. Ogni importo, rilievo e
> conclusione è collegato a una fonte documentale citata (file, foglio, riga, pagina,
> numero documento). Il sistema non sostituisce il giudizio professionale.

---

## 1. Struttura della cartella di lavoro

```text
IVA/{anno}/{periodo}/{cliente}/
├── profilo_cliente.json           (facoltativo: P.IVA, periodicità, pro-rata)
├── 01_xml_fatture/                fatture elettroniche .xml e .xml.p7m
├── 02_registri_iva/               registri vendite, acquisti, sezionali
├── 03_corrispettivi/              registro dei corrispettivi
├── 04_prima_nota/                 prima nota, movimenti banca/cassa/POS
├── 05_bozza_liquidazione/         bozza di liquidazione del gestionale
├── 06_crediti_precedenti_f24_lipe/ F24, LIPE, prospetto credito precedente
└── 99_output_controllo/           generata dal sistema
```

`{periodo}` è `01`..`12` per i mensili, `T1`..`T4` per i trimestrali.

Le cartelle mancanti vengono segnalate nella checklist iniziale. Sono **fonti
essenziali** `02_registri_iva` e `05_bozza_liquidazione`: senza di esse l'esito non può
superare `DA SOSPENDERE`.

---

## 2. Esecuzione

```bash
python src/run_control.py \
  --input  IVA/2026/01/CLIENTE_X \
  --output IVA/2026/01/CLIENTE_X/99_output_controllo
```

Opzioni:

| Opzione | Effetto |
|---|---|
| `--input` | cartella di lavoro del cliente/periodo (obbligatoria) |
| `--output` | cartella di output (default: `<input>/99_output_controllo`) |
| `--config` | configurazione agenti (default: `config/claude-code-agent-teams.json`) |
| `--piva` | P.IVA del cliente, se non deducibile dai documenti |
| `--periodicita` | `mensile` o `trimestrale`, se non deducibile dal percorso |
| `--verbose` | log di dettaglio |

Codice di uscita: `0` se l'esito è `OK` o `DA VERIFICARE`, `1` se `DA SOSPENDERE`,
`2` in caso di errore di configurazione o input.

### Esempio eseguibile

```bash
python src/run_control.py --input examples/cliente_demo/IVA/2026/01/ALFA_SRL
```

```text
Cliente: ALFA_SRL — periodo 01 2026 (2026-01 — 2026-01)
Esito: DA SOSPENDERE
Controllo completo
Anomalie: 1 bloccanti, 7 da verificare, 2 informative
Output in: examples/cliente_demo/IVA/2026/01/ALFA_SRL/99_output_controllo
```

Il cliente dimostrativo contiene **dati fittizi** costruiti per far emergere rilievi
tipici: una fattura attiva non registrata, IVA su autovettura detratta integralmente,
una fattura in reverse charge con integrazione, giorni di corrispettivi mancanti.

---

## 3. Output prodotti

Nella cartella `99_output_controllo/`:

| File | Contenuto |
|---|---|
| `report_controllo_iva.md` | report professionale nelle 7 sezioni obbligatorie |
| `anomalie_iva.xlsx` | tabella delle anomalie lavorabile (colonna `Stato` per lo studio) |
| `dataset_normalizzato.xlsx` | dataset per fonte, quadratura XML/registri, liquidazione ricalcolata, totali per aliquota |
| `fonti_utilizzate.json` | audit trail: file letti, hash, parser, righe estratte, esito lettura |
| `richiesta_documenti_cliente.md` | generato solo se mancano documenti o chiarimenti |

Regola di ripartizione: **il dettaglio tecnico sta negli Excel, la sintesi rilevante nel
report**.

---

## 4. Il team di agenti

| # | Agente | Ruolo | Prefisso rilievi |
|---|---|---|---|
| 1 | `orchestratore-iva` | coordina il controllo, checklist fonti, esito finale | `ORC-` |
| 2 | `intake-normalizzazione-iva` | legge e normalizza XML, P7M, PDF, Excel, CSV | `SRC-` |
| 3 | `fatture-xml-iva` | fatture elettroniche attive e passive, struttura SDI | `XML-` |
| 4 | `registri-iva` | registri vendite/acquisti/corrispettivi, totali e numerazione | `REG-` |
| 5 | `quadratura-xml-registri` | matching XML ↔ registrazioni, mancanti e divergenze | `XML-REG-` |
| 6 | `corrispettivi-iva` | giorni, scorporo IVA, ventilazione, raccordo incassi | `COR-` |
| 7 | `detraibilita-iva` | artt. 19 e 19-bis1 DPR 633/72, IVA indetraibile o parziale | `DET-` |
| 8 | `reverse-split-estero-iva` | reverse charge, TD16-TD19, estero, split payment | `RCS-` |
| 9 | `prima-nota-iva` | F24, Erario c/IVA, credito precedente, compensazioni, incassi | `PN-` |
| 10 | `liquidazione-checker-iva` | ricalcolo della liquidazione e confronto con la bozza | `LIQ-` |
| 11 | `quality-reviewer-iva` | tracciabilità, deduplicazione, gravità, impatti, esito | `QR-` |
| 12 | `report-writer-iva` | report finale e richiesta documenti | — |

Ogni agente è definito in `agents/<nome>.md` con: ruolo, prompt operativo, input attesi,
output attesi, controlli da eseguire e formato delle anomalie. La stessa definizione è
riepilogata in forma programmatica in `config/claude-code-agent-teams.json`.

### Uso da Claude Code

I file di `agents/` hanno il frontmatter dei subagent di Claude Code. Per renderli
disponibili nella sessione:

```bash
mkdir -p .claude/agents
cp iva-control-agent-team/agents/*.md .claude/agents/
```

Poi si invoca l'orchestratore indicando la cartella del cliente, per esempio:

> Usa l'agente `orchestratore-iva` sulla cartella `IVA/2026/01/ALFA_SRL`.

L'orchestratore costruisce la checklist, assegna i controlli agli agenti specialistici e
raccoglie gli output. La libreria Python può essere usata dagli agenti come strumento
(`python src/run_control.py ...`) per la parte quantitativa, riservando al modello le
valutazioni che richiedono giudizio (detraibilità, inerenza, competenza, pro-rata).

---

## 5. Schema delle anomalie

Ogni rilievo, prodotto dal codice o dagli agenti, ha questi campi:

| Campo | Contenuto |
|---|---|
| Codice | prefisso dell'agente + famiglia + progressivo, es. `XML-REG-001.02` |
| Gravità | `Bloccante` / `Da verificare` / `Informativa` |
| Fonte | file, foglio, riga, pagina, numero documento |
| Descrizione | fatto riscontrato, sintetico e verificabile |
| Impatto IVA | importo certo, stima dichiarata come tale, oppure `non determinabile` |
| Azione proposta | `rettifica` / `richiesta documento` / `verifica manuale` / `controllo gestionale` |
| Stato | `Aperto` / `Chiuso` / `Non rilevante` (lavorato dallo studio) |

### Logica dell'esito finale

- **OK** — fonti essenziali presenti, nessuna differenza oltre la tolleranza di
  arrotondamento, solo rilievi informativi.
- **DA VERIFICARE** — anomalie non bloccanti, differenze da chiarire, impatto stimato
  ma non certo, valutazione professionale necessaria.
- **DA SOSPENDERE** — fonti essenziali mancanti, liquidazione non ricalcolabile,
  differenze rilevanti, XML non registrati con impatto, errori certi in liquidazione.

Tolleranze: 0,01 € per riga, 1,00 € per documento; una differenza in liquidazione è
**rilevante** oltre 50,00 € o oltre l'1% dell'IVA esigibile.

---

## 6. Struttura del progetto

```text
iva-control-agent-team/
├── README.md
├── requirements.txt
├── agents/                        12 definizioni di agente
├── config/claude-code-agent-teams.json
├── templates/                     report, anomalie .xlsx, fonti .json
├── src/
│   ├── normalize.py               modello dati e normalizzazioni
│   ├── parse_xml.py               FatturaPA .xml e .xml.p7m
│   ├── parse_excel.py             .xlsx e .csv con mappatura intestazioni
│   ├── parse_pdf.py               estrazione testo e voci di liquidazione
│   ├── controlli.py               controlli tematici degli agenti specialistici
│   ├── match_xml_registri.py      quadratura XML ↔ registri
│   ├── liquidazione_checker.py    ricalcolo e confronto con la bozza
│   ├── export_outputs.py          Excel, JSON, report Markdown
│   └── run_control.py             comando principale
├── tests/test_minimal_flow.py
└── examples/cliente_demo/         cartella dimostrativa con dati fittizi
```

---

## 7. Installazione e test

Il sistema funziona con la sola libreria standard di Python 3.11+. Le dipendenze sono
opzionali e migliorano la copertura:

```bash
pip install -r requirements.txt   # openpyxl (Excel), pdfplumber (PDF)
```

Test:

```bash
python -m unittest discover -s tests -v
```

I test verificano: caricamento della configurazione agenti, creazione delle cartelle di
output, generazione di `fonti_utilizzate.json` e `report_controllo_iva.md`, gestione del
caso "fonti mancanti" con esito `DA SOSPENDERE`, presenza dell'agente
`report-writer-iva`, normalizzazioni di base e riproducibilità dell'esempio.

---

## 8. Limiti del sistema

Da tenere presenti prima di usare il sistema in produzione.

1. **PDF**: senza `pdfplumber` o `pypdf` i PDF non vengono letti e sono dichiarati
   `non_letto` nell'audit trail. Anche con il backend installato, l'estrazione di righe
   di registro da PDF è euristica e ogni riga è marcata "dato da verificare"; i PDF
   scansionati richiedono OCR o il file nativo.
2. **P7M**: il contenuto XML viene estratto dalla busta CAdES senza verificare la firma
   digitale. Il sistema legge, non attesta l'autenticità del documento.
3. **Intestazioni dei registri**: la mappatura delle colonne copre i sinonimi più diffusi
   dei gestionali italiani. Un tracciato non riconosciuto produce un rilievo `SRC-003`,
   non un'interpretazione arbitraria — ma va aggiunto ai sinonimi in `parse_excel.py`.
4. **Classificazione dei registri**: vendite/acquisti sono dedotti dal nome del file. Un
   nome non riconoscibile genera un rilievo e le righe restano fuori dai totali.
5. **Detraibilità**: l'individuazione è basata su parole chiave nelle descrizioni e sulle
   controparti. Produce rilievi `Da verificare`, mai verdetti: l'inerenza, l'uso effettivo
   e il pro-rata non sono desumibili dai documenti contabili.
6. **Reverse charge e split payment**: il codice verifica la doppia annotazione e la
   coerenza degli importi. La qualificazione dell'operazione (soggetta o meno a
   inversione contabile) resta una valutazione professionale.
7. **Corrispettivi**: i giorni mancanti sono segnalati con la loro ricorrenza settimanale,
   ma il sistema non conosce il calendario di chiusura del cliente; la stima di impatto
   sulla media giornaliera è dichiarata come stima.
8. **Prima nota e F24**: la riconciliazione copre i casi ricostruibili dai file forniti.
   Il controllo dei limiti di compensazione e della tempestività dei versamenti richiede
   dati (cassetto fiscale, scadenze effettive) non presenti nella cartella.
9. **Ventilazione, plafond, pro-rata**: riconosciuti solo se documentati; il sistema non
   li applica d'ufficio.
10. **Nessun invio a servizi esterni**: l'elaborazione è locale. I dati del cliente non
    vengono trasmessi ad alcun servizio di terze parti dalla libreria.

---

*Sistema di supporto al controllo IVA. L'esito prodotto è una proposta motivata e
tracciabile: la responsabilità della liquidazione resta del professionista incaricato.*
