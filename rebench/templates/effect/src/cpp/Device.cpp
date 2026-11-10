#include "Device.h"

#include <algorithm>
#include <cmath>

namespace @@CPP_NAMESPACE@@ {

Device::Device(double sampleRate) : mSampleRate(sampleRate) {}

void Device::resolveRefs() {
  mCustomProps = JBox_GetMotherboardObjectRef("/custom_properties");
  mInLeft   = JBox_GetMotherboardObjectRef("/audio_inputs/inLeft");
  mInRight  = JBox_GetMotherboardObjectRef("/audio_inputs/inRight");
  mOutLeft  = JBox_GetMotherboardObjectRef("/audio_outputs/outLeft");
  mOutRight = JBox_GetMotherboardObjectRef("/audio_outputs/outRight");
  mPropLevel = JBox_MakePropertyRef(mCustomProps, "level");
  mRefsResolved = true;
}

bool Device::inputConnected(TJBox_ObjectRef input) const {
  return JBox_GetBoolean(JBox_LoadMOMPropertyByTag(input, kJBox_AudioInputConnected)) != 0;
}

float Device::readInput(TJBox_ObjectRef input, float* dst) const {
  std::fill(dst, dst + kBatchFrames, 0.0f);
  if (!inputConnected(input)) return 0.0f;
  const TJBox_Value buf = JBox_LoadMOMPropertyByTag(input, kJBox_AudioInputBuffer);
  const TJBox_DSPBufferInfo info = JBox_GetDSPBufferInfo(buf);
  const int n = std::min<int>(kBatchFrames, static_cast<int>(info.fSampleCount));
  JBox_GetDSPBufferData(buf, 0, n, dst);
  float peak = 0.0f;
  for (int i = 0; i < n; ++i) peak = std::max(peak, std::fabs(dst[i]));
  return peak;
}

void Device::writeOutput(TJBox_ObjectRef output, const float* src) const {
  TJBox_Value buf = JBox_LoadMOMPropertyByTag(output, kJBox_AudioOutputBuffer);
  const TJBox_DSPBufferInfo info = JBox_GetDSPBufferInfo(buf);
  const int n = std::min<int>(kBatchFrames, static_cast<int>(info.fSampleCount));
  JBox_SetDSPBufferData(buf, 0, n, src);
}

void Device::renderBatch(const TJBox_PropertyDiff[], TJBox_UInt32) {
  if (!mRefsResolved) resolveRefs();

  // Effects must follow the host's On / Off / Bypass switch.
  const auto state = static_cast<TJBox_OnOffBypassStates>(static_cast<int>(JBox_GetNumber(
      JBox_LoadMOMPropertyByTag(mCustomProps, kJBox_CustomPropertiesOnOffBypass))));
  if (state == kJBox_EnabledOff) return;  // unwritten outputs count as silent

  float left[kBatchFrames], right[kBatchFrames];
  const float peakL = readInput(mInLeft, left);
  float peakR = readInput(mInRight, right);
  const bool monoIn = inputConnected(mInLeft) && !inputConnected(mInRight);
  if (monoIn) {
    std::copy(left, left + kBatchFrames, right);
    peakR = peakL;
  }
  if (peakL < kJBox_SilentThreshold && peakR < kJBox_SilentThreshold) return;

  if (state != kJBox_EnabledBypass) {
    // Level: 0..1, 0.5 = unity, 1 = +6 dB.
    const float gain = static_cast<float>(2.0 * JBox_GetNumber(JBox_LoadMOMProperty(mPropLevel)));
    for (int i = 0; i < kBatchFrames; ++i) {
      left[i] *= gain;
      right[i] *= gain;
    }
  }
  writeOutput(mOutLeft, left);
  if (!monoIn) writeOutput(mOutRight, right);
}

} // namespace @@CPP_NAMESPACE@@
