#include "Device.h"

#include <algorithm>
#include <cmath>

namespace @@CPP_NAMESPACE@@ {

namespace {
constexpr double kTwoPi = 6.283185307179586;
constexpr double kAttackSeconds = 0.005;
constexpr double kReleaseSeconds = 0.150;
}  // namespace

Device::Device(double sampleRate) : mSampleRate(sampleRate) {}

void Device::resolveRefs() {
  mCustomProps = JBox_GetMotherboardObjectRef("/custom_properties");
  mNoteStates  = JBox_GetMotherboardObjectRef("/note_states");
  mTransport   = JBox_GetMotherboardObjectRef("/transport");
  mOutLeft  = JBox_GetMotherboardObjectRef("/audio_outputs/outLeft");
  mOutRight = JBox_GetMotherboardObjectRef("/audio_outputs/outRight");
  mPropLevel = JBox_MakePropertyRef(mCustomProps, "level");
  mRefsResolved = true;
}

void Device::handleNotes(const TJBox_PropertyDiff iPropertyDiffs[], TJBox_UInt32 iDiffCount) {
  for (TJBox_UInt32 i = 0; i < iDiffCount; ++i) {
    if (iPropertyDiffs[i].fPropertyRef.fObject != mNoteStates) continue;
    const TJBox_NoteEvent event = JBox_AsNoteEvent(iPropertyDiffs[i]);
    const int note = static_cast<int>(event.fNoteNumber);
    if (note < 0 || note > 127) continue;
    if (event.fVelocity > 0) {
      mHeld[note] = true;
      mNote = note;
      mVelocity = event.fVelocity / 127.0;
      mGate = true;
    } else {
      mHeld[note] = false;
      if (note == mNote) {
        // Fall back to the highest note still held, else release.
        int fallback = -1;
        for (int n = 127; n >= 0; --n) {
          if (mHeld[n]) { fallback = n; break; }
        }
        if (fallback >= 0) mNote = fallback;
        else mGate = false;
      }
    }
  }
}

void Device::writeOutput(TJBox_ObjectRef output, const float* src) const {
  TJBox_Value buf = JBox_LoadMOMPropertyByTag(output, kJBox_AudioOutputBuffer);
  const TJBox_DSPBufferInfo info = JBox_GetDSPBufferInfo(buf);
  const int n = std::min<int>(kBatchFrames, static_cast<int>(info.fSampleCount));
  JBox_SetDSPBufferData(buf, 0, n, src);
}

void Device::renderBatch(const TJBox_PropertyDiff iPropertyDiffs[], TJBox_UInt32 iDiffCount) {
  if (!mRefsResolved) resolveRefs();

  const TJBox_Float64 reset = JBox_GetNumber(
      JBox_LoadMOMPropertyByTag(mTransport, kJBox_TransportRequestResetAudio));
  if (reset != mResetCounter) {
    mResetCounter = reset;
    std::fill(mHeld, mHeld + 128, false);
    mNote = -1;
    mGate = false;
    mEnvelope = 0.0;
  }
  handleNotes(iPropertyDiffs, iDiffCount);
  if (mNote < 0 || (!mGate && mEnvelope <= 0.0)) return;  // silent: write nothing

  // Level: 0..1, 0.5 = unity, 1 = +6 dB.
  const double gain = 2.0 * JBox_GetNumber(JBox_LoadMOMProperty(mPropLevel));
  const double freq = 440.0 * std::pow(2.0, (mNote - 69) / 12.0);
  const double step = freq / mSampleRate;
  const double attack = 1.0 / (kAttackSeconds * mSampleRate);
  const double release = 1.0 / (kReleaseSeconds * mSampleRate);

  float out[kBatchFrames];
  for (int i = 0; i < kBatchFrames; ++i) {
    mEnvelope = mGate ? std::min(1.0, mEnvelope + attack) : std::max(0.0, mEnvelope - release);
    out[i] = static_cast<float>(std::sin(kTwoPi * mPhase) * mEnvelope * mVelocity * gain * 0.5);
    mPhase += step;
    if (mPhase >= 1.0) mPhase -= 1.0;
  }
  if (!mGate && mEnvelope <= 0.0) mNote = -1;
  writeOutput(mOutLeft, out);
  writeOutput(mOutRight, out);
}

} // namespace @@CPP_NAMESPACE@@
