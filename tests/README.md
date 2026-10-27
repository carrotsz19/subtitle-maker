# Tests

Run on Windows by `.github/workflows/windows.yml` on every push and pull request, after
`Setup.bat --no-window` has installed everything the way it does on a user's PC.

- `test_units.py`: no network. Line breaking, never overwriting, the GPU-to-CPU fallback, the
  page API surface (the startup freeze), label lengths.
- `test_pipeline.py`: the real thing. Downloads the Potato model with `--prepare`, transcribes
  a speech clip, detects two speakers, and checks the worker keeps the model loaded.
- `test_window.py`: opens the real window (`app.py --smoke-test`) and fails if it doesn't get
  ready in time, printing `startup.log`.

They can also run locally with the program's Python (`%USERPROFILE%\.subtitle-maker\venv\Scripts\python.exe`)
and `SUBTITLE_MAKER_HOME` pointing at a scratch folder, so your own settings and models aren't touched.

`media/jfk.flac` is 11 seconds of John F. Kennedy's 1961 inaugural address (public domain), the
same clip Whisper's own tests use.
