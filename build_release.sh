#!/bin/bash
# Build release zip for HACS
# Run before creating a GitHub release

rm -f opinet_price.zip
mkdir -p build/custom_components/opinet_price
cp custom_components/opinet_price/*.py build/custom_components/opinet_price/
cp custom_components/opinet_price/manifest.json build/custom_components/opinet_price/
cp icon.png build/custom_components/opinet_price/ 2>/dev/null || true
cd build/custom_components && zip -r ../../opinet_price.zip opinet_price/ && cd ../..
rm -rf build
echo "Done: opinet_price.zip ($(stat -c%s opinet_price.zip) bytes)"
