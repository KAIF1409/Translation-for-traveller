import 'dart:async';
import 'dart:typed_data';

import 'package:audioplayers/audioplayers.dart';

/// Playback states reported by [PlaybackService].
enum PlaybackState { idle, playing, completed, failed }

/// Plays synthesized WAV bytes received from the backend.
///
/// The backend sends a complete WAV file as a single binary WebSocket
/// frame; this service feeds it to audioplayers via BytesSource.
class PlaybackService {
  PlaybackService({
    required this.onStateChange,
  });

  final void Function(PlaybackState state) onStateChange;

  final AudioPlayer _player = AudioPlayer();
  StreamSubscription<void>? _completeSub;
  bool _disposed = false;

  PlaybackState _state = PlaybackState.idle;

  PlaybackState get state => _state;

  /// Plays [wavBytes] (a complete WAV file). Returns false on failure.
  Future<bool> play(List<int> wavBytes) async {
    if (_disposed || wavBytes.isEmpty) return false;
    try {
      await _completeSub?.cancel();
      _completeSub = _player.onPlayerComplete.listen((_) {
        _setState(PlaybackState.completed);
      });
      await _player.stop();
      await _player.play(BytesSource(Uint8List.fromList(wavBytes)));
      _setState(PlaybackState.playing);
      return true;
    } on Exception catch (_) {
      _setState(PlaybackState.failed);
      return false;
    }
  }

  /// Stops playback and releases the player (backgrounding/disposal).
  Future<void> dispose() async {
    _disposed = true;
    await _completeSub?.cancel();
    _completeSub = null;
    await _player.dispose();
  }

  void _setState(PlaybackState newState) {
    if (_state == newState) return;
    _state = newState;
    onStateChange(newState);
  }
}
