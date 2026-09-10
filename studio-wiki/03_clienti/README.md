# 03 — Clienti

Una sottocartella per cliente, con una scheda sintetica e le note di lavoro.

## Struttura consigliata

```
03_clienti/
  NomeCliente/
    scheda.md            # anagrafica essenziale, regimi, incarichi, referenti
    note.md              # decisioni, chiarimenti, storico questioni aperte
    scadenze.md          # scadenze specifiche del cliente
```

## Cosa mettere nella `scheda.md`

- Ragione sociale / forma giuridica
- Regime fiscale e contabile (IVA mensile/trimestrale, forfettario, ecc.)
- Incarichi dello Studio (tenuta contabilità, revisione, sindaco, OdV 231…)
- Referenti e contatti utili
- Particolarità ricorrenti (es. reverse charge, split payment, esteri)

## Principio di minimizzazione

Riportare **solo** i dati necessari al lavoro. Evitare di duplicare qui documenti o
dati sensibili che vivono già nel gestionale/archivio dello Studio.

> Nota: se preferisci non versionare le note dei clienti nel repository, si può
> aggiungere `03_clienti/*/` al `.gitignore`. Chiedimelo e lo imposto.
