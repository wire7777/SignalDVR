#!/bin/bash

cd "$(dirname "$0")/.."

rm -f plugin.video.signaldvr.zip

zip -r plugin.video.signaldvr.zip plugin.video.signaldvr
