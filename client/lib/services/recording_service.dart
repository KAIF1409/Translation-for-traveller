import 'dart:async';

import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';

/// Outcome of requesting microphone permission.
enum MicPermission { granted, denied, permanentlyDenied }

/// Errors that can occur while starting/stopping capture.
enum RecordingError {
  permissionDenied,
  permissionPermanentlyDenied,
  unsupportedFormat,
  streamFailed,
}

/// Streams raw PCM16 mono 16 kHz microphone audio.
///
/// Protocol contract with the backend: binary frames are raw PCM16 LE
/// samples (no WAV headers). The `record` package is configured with
/// `AudioEncoder.pcm16bits`; if the platform cannot deliver that encoding,
/// [onError] reports [RecordingError.unsupportedFormat] instead of sending
/// mislabeled compressed bytes (e.g. AAC/M4A) as PCM.
class RecordingService {
  RecordingService({
    required this.onChunk,
    required this.onError,
    required this.onStopped,
  });

  static const int sampleRate = 16000;
  static const int numChannels = 1;

  final void Function(List<int> pcmBytes) onChunk;
  final void Function(RecordingError error) onError;
  final void Function() onStopped;

  final AudioRecorder _recorder = AudioRecorder();
  StreamSubscription<Uint8ListX>? _subscription;
  bool _isRecording = false;

  bool get isRecording => _isRecording;

  /// Requests microphone permission. Returns the outcome.
  Future<MicPermission> ensurePermission() async {
    final status = await Permission.microphone.status;
    if (status.isGranted) return MicPermission.granted;
    if (status.isPermanentlyDenied) {
      return MicPermission.permanentlyDenied;
    }
    final result = await Permission.microphone.request();
    if (result.isGranted) return MicPermission.granted;
    if (result.isPermanentlyDenied) {
      return MicPermission.permanentlyDenied;
    }
    return MicPermission.denied;
  }

  /// Starts streaming PCM16 mono 16 kHz. Returns false on failure.
  Future<bool> start() async {
    if (_isRecording) return true;

    final permission = await ensurePermission();
    if (permission == MicPermission.denied) {
      onError(RecordingError.permissionDenied);
      return false;
    }
    if (permission == MicPermission.permanentlyDenied) {
      onError(RecordingError.permissionPermanentlyDenied);
      return false;
    }

    try {
      if (!await _recorder.hasPermission()) {
        onError(RecordingError.permissionDenied);
        return false;
      }
      final stream = await _recorder.startStream(
        const RecordConfig(
          encoder: AudioEncoder.pcm16bits,
          sampleRate: sampleRate,
          numChannels: numChannels,
          bitRate: sampleRate * 16, // 16-bit mono
        ),
      );
      _subscription = stream.listen(
        (chunk) => onChunk(chunk),
        onError: (Object e) {
          onError(RecordingError.streamFailed);
          stop();
        },
        onDone: () {
          _isRecording = false;
          onStopped();
        },
        cancelOnError: true,
      );
      _isRecording = true;
      return true;
    } on Exception catch (_) {
      // Some platforms/devices reject raw PCM streams.
      onError(RecordingError.unsupportedFormat);
      return false;
    } catch (_) {
      onError(RecordingError.streamFailed);
      return false;
    }
  }

  /// Stops capture and releases the stream. Safe to call repeatedly.
  Future<void> stop() async {
    _subscription?.cancel();
    _subscription = null;
    if (_isRecording) {
      _isRecording = false;
      try {
        await _recorder.stop();
      } catch (_) {
        // Ignore stop failures during teardown.
      }
      onStopped();
    }
  }

  /// Fully releases resources (widget disposal / app backgrounding).
  Future<void> dispose() async {
    await stop();
    await _recorder.dispose();
  }
}

/// Alias so the subscription type matches record's stream element type
/// without importing record internals beyond the public API.
typedef Uint8ListX = dynamic;
