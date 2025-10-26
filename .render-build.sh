#!/usr/bin/env bash
# install system deps
apt-get update && apt-get install -y qpdf libqpdf-dev
# now install python deps
pip install -r requirements.txt
