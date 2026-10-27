# SafeVoice

An anti-ragging and cyberbullying reporting tool for college campuses. A student pastes a message they received, and the app checks whether it is bullying, estimates how severe it is, and generates a formal incident report that can be submitted to the college anti-ragging cell.

Live demo: https://antiragging-detection-project-jgphtm8i7aexaccxvb4skn.streamlit.app/

## Overview

The app takes a single message as input and runs it through two transformer models:

1. A DistilBERT toxicity classifier decides whether the message is cyberbullying.
2. A zero-shot DistilBART model assigns context labels (threat, ragging, self-harm risk, etc.).
3. A severity score from 0 to 100% is computed and mapped to one of four levels, from Low to Critical.
4. LIME highlights the words that contributed most to the prediction.
5. A formal incident report is generated from the results.
6. If the risk level is Critical, the counselor is alerted.

The tool is victim-initiated. Nothing is monitored automatically, and nothing is stored after the analysis.

## Installation

Requires Python 3.11.

```bash
git clone https://github.com/Aryan-710/AntiRagging.git
cd AntiRagging

python -m venv venv
source venv/bin/activate      # macOS/Linux
# venv\Scripts\activate       # Windows

pip install -r requirements.txt
```

## Usage

```bash
streamlit run SafeVoice_app.py
```

The app opens in your browser at `http://localhost:8501`.

## Deployment

**Streamlit Cloud:** push the repo to GitHub, create a new app on [share.streamlit.io](https://share.streamlit.io), and select `SafeVoice_app.py` as the main file. `runtime.txt`, `packages.txt` and `.streamlit/config.toml` are already included.

**Heroku / Railway:** the repo includes a `Procfile`.

```bash
git push heroku main
```

## Models

| Component | Model |
|---|---|
| Toxicity classifier | `martin-ha/toxic-comment-model` (DistilBERT, ~66M parameters) |
| Context classifier | `valhalla/distilbart-mnli-12-3` (zero-shot, multi-label) |
| Explainability | LIME (word-level) |
| Severity score | 50% model probability, 50% weighted context flags |

Context labels:

- Insult / harassment
- Direct threat
- Academic / hierarchy abuse
- Severe ragging / hazing
- Suicide / self-harm risk

## Security

- Input is sanitized: HTML tags are stripped with `bleach`, control characters are removed and unicode is normalized.
- User-derived content is escaped with `html.escape()` before rendering.
- Rate limit of 10 analyses per minute per session.
- XSRF protection is enabled in `.streamlit/config.toml`.
- No user data is stored; analysis is not persisted.
- Dependencies are pinned to exact versions.
- Server-side validation rejects over-length, URL-only and whitespace-only input.

## Project Structure

```
AntiRagging/
├── SafeVoice_app.py                              # Streamlit app
├── requirements.txt
├── Procfile
├── runtime.txt
├── packages.txt
├── .streamlit/
│   ├── config.toml
│   └── secrets.toml.example
├── models/                                        # Legacy TF-IDF models (gitignored)
├── hinglish_cyberbullying_dataset.csv
├── Cyberbullying_SuicidePrevention_Review2.ipynb  # Research notebook
└── README.md
```

## Datasets

- [Cyberbullying Classification (Kaggle)](https://www.kaggle.com/): 47,692 labelled tweets across 6 categories.
- `hinglish_cyberbullying_dataset.csv`: a custom Hinglish dataset for the Indian college context.

## References

- Cyberbullying Detection using NLP, ScienceDirect, 2025 (baseline approach)
- Hybrid Models for Cyberbullying, MDPI Electronics, 2021 (model selection)
- Suicidal Ideation Detection, MDPI IJERPH, 2022 (severity scoring)
- Ribeiro et al., "Why Should I Trust You?", KDD 2016 (LIME)
- Sentence Transformer Fine-Tuning, Springer, 2024 (future work)

## Roadmap

- **Phase 1:** fine-tune DistilBERT and build a multi-class classifier for 6 bullying types.
- **Phase 2:** FastAPI backend, browser extension, counselor SMS/email alerts.
- **Phase 3:** multi-modal input (image + text via CLIP) and Hinglish support (IndicBERT).

## Helplines (India)

| Service | Contact |
|---|---|
| Anti-Ragging Helpline | 1800-180-5522 |
| iCall | 9152987821 |
| Vandrevala Foundation | 1860-2662-345 |
| iCall Chat | https://icallhelpline.org |

## License

Released under the MIT License. See [LICENSE](LICENSE).
