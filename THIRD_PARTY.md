# Third-party dependencies and licensing

This project contains original MIT-licensed Python source code. The Windows build downloads dependencies and native executables when GitHub Actions runs. Distribution must comply with each upstream license.

- FFmpeg from BtbN/FFmpeg-Builds: the selected x64 GPL binary includes GPL components (such as libx264). **Do not present the complete bundled distribution as MIT-only**. Preserve upstream license notices and meet applicable GPL redistribution/source-offer obligations. See https://ffmpeg.org/legal.html and https://github.com/BtbN/FFmpeg-Builds.
- llama.cpp: upstream release and component notices apply. See https://github.com/ggml-org/llama.cpp.
- PySide6/Qt: LGPLv3/commercial licensing terms apply; the onedir distribution retains DLLs so they can be replaced as required by the applicable LGPL terms. See https://doc.qt.io/qtforpython-6/licenses.html.
- faster-whisper, CTranslate2, PyAV, sounddevice and other Python wheels: their own open-source notices and binary licenses apply. Review their license files before redistribution.
- Hugging Face model weights are NOT included in this repository or release artifact. Each downloaded model has its own terms: Whisper and Qwen2.5 via their upstream model cards.
- Windows built-in speech via System.Speech: subject to applicable Windows platform terms.

If publishing builds commercially, undertake a complete dependency/license audit and include notices and any required corresponding source and license documents. This project does not grant rights over users' uploaded/source video, music, fonts or footage.
