# First Screen Blank vs. Sample Data — Flagsmith Experiment

Built for **Toggle Wars ([Track 1] Test Something Real)** to test whether showing sample data on a user's first screen increases `main_action_clicked` conversions compared to a blank first screen.

## How Flagsmith Is Used
- **Experimentation & Multivariate Flag (`first_screen_mode`):** Evaluates users across `Control` (blank workspace with `Create First Item`) and `Variant_1` (sample templates with `Use Sample Workspace`), configured in Flagsmith Experiment `1118` (`100%` rollout, `50%` Control / `50%` Variant_1).
- **Event Tracking:** Tracks exposure and `main_action_clicked` conversion events per user ID via Flagsmith.

## Setup & Run Locally
1. Clone `https://github.com/km26257-dev/toggle-wars-first-screen-experiment.git`.
2. Optional: install `flagsmith` (`pip install flagsmith`) and set `FLAGSMITH_ENVIRONMENT_KEY` in your environment.
3. Run `python app.py` and open `http://127.0.0.1:8000/`.
