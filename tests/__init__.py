import os

if os.environ.get("RUN_OPENAI") != "1":
    from engine import openai_api

    def _no_network(request):
        raise AssertionError("offline tests must not call the OpenAI API")

    openai_api.DEFAULT_TRANSPORT = _no_network

if os.environ.get("RUN_HEYGEN") != "1":
    from engine import heygen_api

    def _no_heygen_network(request):
        raise AssertionError("offline tests must not call the HeyGen API")

    heygen_api.DEFAULT_TRANSPORT = _no_heygen_network
