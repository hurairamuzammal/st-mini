@echo off
echo Creating assets directory...
if not exist "assets" mkdir assets
cd assets

echo Downloading Parakeet TDT model...
curl -L -O https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2

echo Extracting model...
tar -xf sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2

echo Cleaning up...
del sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2

cd ..
echo Done! Model is ready in assets/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8
pause