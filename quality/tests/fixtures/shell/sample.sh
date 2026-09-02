#!/bin/sh
# Conformance fixture: known escapes.
rm -f x || true
set +e
# shellcheck disable=SC2086
echo $y
