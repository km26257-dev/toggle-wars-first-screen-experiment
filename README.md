# First Screen Blank vs Sample Data Experiment

Flagsmith project **56725** (`first screen blank vs sample data testing`), Development environment (`jKqRNoHNf4bJcXMhnWEoPt`).

- **Feature flag:** `first_screen_mode` (`"blank"` vs `"sample data"`)
- **Conversion event:** `main_action_clicked`

## Setup

```bash
pip install -r requirements.txt
```

## Run Unit Tests

```bash
python -m pytest
```

## Simulate Traffic

```bash
python first_screen_app.py simulate --users 40
```

## Launch Local HTML/JS Demo

```bash
python first_screen_app.py serve --port 8000
```

Then open `http://127.0.0.1:8000` in your browser to switch users, view the assigned first screen, and click the main action button to record `main_action_clicked`.
