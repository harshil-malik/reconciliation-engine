"""Desktop-app packaging layer for the Reconciliation Engine.

Everything in this package is about turning the FastAPI + llama.cpp app into a
double-clickable Windows program: locating bundled assets (`resources`), starting
and stopping the two local model servers (`llama_supervisor`), and wiring the
backend to a native window (`launcher`). None of it touches the reconciliation
pipeline itself — `app/` is imported unchanged.
"""
