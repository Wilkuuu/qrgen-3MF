# qrgen-3MF

Lokalny panel do tabliczek wielokolorowych: podstawa, kod QR prowadzący do linku i tekst. Wynik to jeden plik 3MF z osobnym obiektem na każdy kolor.

## Uruchomienie

Dwuklik `uruchom.bat` albo:

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

Panel otwiera się w przeglądarce, zwykle pod `http://127.0.0.1:5000`.

W slicerze (Bambu Studio, OrcaSlicer) otwórz plik jako projekt, żeby wczytać kolory filamentów. Jeśli kolory się nie podepną, obiekty `Podstawa`, `QR` i `Tekst` i tak są rozdzielone i można im ręcznie wskazać filament.

Dodatkowe fonty: wrzuć pliki `.ttf` lub `.otf` do folderu `fonts`.
