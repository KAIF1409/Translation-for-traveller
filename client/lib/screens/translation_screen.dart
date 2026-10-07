import 'dart:async';

import 'package:flutter/material.dart';

import '../services/connection_service.dart';
import '../services/playback_service.dart';
import '../services/recording_service.dart';
import '../services/turn_state_service.dart';
import '../widgets/countdown_ring.dart';

/// Supported target/review languages (mirror backend allowlist).
const List<String> kLanguages = ['ja', 'ko', 'hi', 'vi', 'en'];
const List<String> kTones = ['formal', 'business', 'casual'];

class TranslationScreen extends StatefulWidget {
  const TranslationScreen({super.key});

  @override
  State<TranslationScreen> createState() => _TranslationScreenState();
}

class _TranslationScreenState extends State<TranslationScreen>
    with WidgetsBindingObserver {
  // ---- Services ----
  late final ConnectionService _connection;
  late final RecordingService _recorder;
  late final PlaybackService _player;
  late final TurnController _turn;

  // ---- Settings ----
  final TextEditingController _urlController = TextEditingController(
    text: 'ws://10.0.2.2:8000/ws',
  );
  final TextEditingController _tokenController = TextEditingController();
  String _targetLanguage = 'ja';
  String _reviewLanguage = 'en';
  String _tone = 'business';

  // ---- UI state ----
  ConnState _connState = ConnState.disconnected;
  TurnPhase _phase = TurnPhase.idle;
  String? _transcript;
  String? _translated;
  String? _backTranslation;
  String? _statusMessage;
  bool _playbackFailed = false;

  // ---- Room state (two-phone sessions) ----
  final TextEditingController _roomIdController = TextEditingController();
  final TextEditingController _joinSecretController = TextEditingController();
  String? _joinedRoomId;
  String? _myParticipantId;
  String? _sharedJoinSecret; // creator-only: shown so it can be shared
  bool _peerInRoom = false;
  bool _roomBusy = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);

    _connection = ConnectionService(
      onJsonMessage: _onJsonMessage,
      onBinaryMessage: _onBinaryMessage,
      onStateChange: (state) {
        if (!mounted) return;
        setState(() {
          _connState = state;
          if (state == ConnState.disconnected) {
            // Room membership is per-connection on the server; drop stale
            // room UI state so a reconnect starts fresh.
            _joinedRoomId = null;
            _myParticipantId = null;
            _sharedJoinSecret = null;
            _peerInRoom = false;
            _roomBusy = false;
          }
        });
      },
    );
    _recorder = RecordingService(
      onChunk: _sendAudioChunk,
      onError: _onRecordingError,
      onStopped: () {},
    );
    _player = PlaybackService(
      onStateChange: (state) {
        if (!mounted) return;
        setState(() {
          _playbackFailed = state == PlaybackState.failed;
        });
        // Room mode: the listener must acknowledge playback so the server
        // releases the room turn (half-duplex policy).
        if (state == PlaybackState.completed) {
          _connection.sendJson({'type': 'playback_complete'});
        }
      },
    );
    _turn = TurnController(
      callbacks: TurnCallbacks(
        onPhaseChanged: (phase) {
          if (!mounted) return;
          setState(() => _phase = phase);
        },
        onTurnEnded: _onTurnEnded,
        onCountdownExpired: _approve,
        onError: (code, message) => _setStatus('Error ($code): $message'),
      ),
    );
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Cancel countdown when backgrounding — never approve while unseen.
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.hidden) {
      _turn.cancelCountdown();
      _recorder.stop();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _turn.dispose();
    _recorder.dispose();
    _player.dispose();
    _connection.disconnect();
    _urlController.dispose();
    _tokenController.dispose();
    _roomIdController.dispose();
    _joinSecretController.dispose();
    super.dispose();
  }

  // ---- Backend message handling ----

  void _onJsonMessage(Map<String, dynamic> msg) {
    final type = msg['type'] as String? ?? '';
    switch (type) {
      case 'ready':
        _setStatus('Connected to backend.');
        // Never replay an approval or old turn after a reconnect.
        if (_turn.isTurnActive) {
          _turn.onTurnEnded('reconnected');
        }
        break;
      case 'processing':
        _turn.onProcessing();
        _setStatus('Processing audio...');
        break;
      case 'review':
        _turn.onReview(ReviewInfo(
          utteranceId: msg['utterance_id'] as String? ?? '',
          transcript: msg['transcript'] as String? ?? '',
          translatedText: msg['translated_text'] as String? ?? '',
          backTranslation: msg['back_translation'] as String? ?? '',
          targetLanguage: msg['target_language'] as String? ?? _targetLanguage,
          verificationWindowMs:
              (msg['verification_window_ms'] as num?)?.toInt() ?? 800,
        ));
        setState(() {
          _transcript = _turn.pendingReview?.transcript;
          _translated = _turn.pendingReview?.translatedText;
          _backTranslation = _turn.pendingReview?.backTranslation;
        });
        break;
      case 'turn_end':
        _turn.onTurnEnded(msg['reason'] as String? ?? 'unknown');
        break;
      case 'error':
        final code = msg['code'] as String? ?? 'unknown';
        final message = msg['message'] as String? ?? '';
        _setStatus('Backend error ($code): $message');
        _turn.onTurnEnded('error');
        break;
      // ---- Room session messages ----
      case 'room_created':
        setState(() {
          _joinedRoomId = msg['room_id'] as String?;
          _myParticipantId = msg['participant_id'] as String?;
          _sharedJoinSecret = msg['join_secret'] as String?;
          _peerInRoom = false;
        });
        _setStatus('Room created. Share the ID + secret with the other phone.');
        break;
      case 'joined':
        setState(() {
          _joinedRoomId = msg['room_id'] as String?;
          _myParticipantId = msg['participant_id'] as String?;
          _peerInRoom = false;
        });
        _setStatus(
            (msg['rejoined'] as bool? ?? false)
                ? 'Rejoined room $_joinedRoomId.'
                : 'Joined room $_joinedRoomId. Share the ID + secret.',
            );
        break;
      case 'peer_joined':
        setState(() => _peerInRoom = true);
        _setStatus('Peer joined the room. You can take turns now.');
        break;
      case 'peer_left':
        setState(() => _peerInRoom = false);
        _setStatus('Peer left the room. Waiting for them to rejoin...');
        break;
      case 'busy':
        setState(() => _roomBusy = true);
        _setStatus('Other person is speaking. Wait for your turn.');
        Future.delayed(const Duration(seconds: 3), () {
          if (mounted) setState(() => _roomBusy = false);
        });
        break;
      case 'local_error':
        // Connection-level notice (e.g. reconnect attempts exhausted).
        _setStatus(msg['message'] as String? ?? 'Connection issue.');
        break;
      default:
        break;
    }
  }

  void _onBinaryMessage(List<int> bytes) {
    // Binary frame = synthesized WAV audio. Play it.
    setState(() => _statusMessage = 'Receiving audio...');
    _player.play(bytes);
  }

  void _onRecordingError(RecordingError error) {
    switch (error) {
      case RecordingError.permissionDenied:
        _setStatus('Microphone permission denied. Enable it in Settings.');
        break;
      case RecordingError.permissionPermanentlyDenied:
        _setStatus('Microphone permission permanently denied.');
        break;
      case RecordingError.unsupportedFormat:
        _setStatus('Device cannot stream raw PCM16. Report this format issue.');
        break;
      case RecordingError.streamFailed:
        _setStatus('Recording stream failed.');
        break;
    }
  }

  void _setStatus(String message) {
    if (!mounted) return;
    setState(() => _statusMessage = message);
  }

  void _sendAudioChunk(List<int> pcmBytes) {
    if (_connection.state == ConnState.connected &&
        _turn.phase == TurnPhase.listening) {
      // Send raw PCM16 bytes as a binary WebSocket frame.
      _connection.sendBinary(pcmBytes);
    }
  }

  Future<void> _connect() async {
    try {
      await _connection.connect(
        _urlController.text,
        accessToken:
            _tokenController.text.isEmpty ? null : _tokenController.text,
      );
    } on Exception catch (e) {
      _setStatus('Connection failed: $e');
    }
  }

  Future<void> _startRecording() async {
    if (_connState != ConnState.connected) {
      _setStatus('Not connected. Configure server URL and connect first.');
      return;
    }
    if (_turn.isTurnActive) {
      _setStatus('A turn is already active.');
      return;
    }
    _connection.sendJson({
      'type': 'start',
      'target_language': _targetLanguage,
      'tone': _tone,
      'review_language': _reviewLanguage,
    });
    _turn.startTurn();
    final ok = await _recorder.start();
    if (!ok) {
      _setStatus('Could not start recording.');
      _connection.sendJson({'type': 'cancel', 'utterance_id': ''});
      _turn.markCancelled();
    }
  }

  Future<void> _stopRecording() async {
    await _recorder.stop();
  }

  void _approve() {
    final review = _turn.pendingReview;
    if (review == null) return;
    if (!_turn.markApprovalSent()) return;
    _connection.sendJson({
      'type': 'approve',
      'utterance_id': review.utteranceId,
    });
    _setStatus('Approved. Synthesizing speech...');
  }

  void _cancelTurn() {
    final review = _turn.pendingReview;
    final utteranceId = review?.utteranceId ?? '';
    if (!_turn.markCancelled()) return;
    _connection.sendJson({
      'type': 'cancel',
      'utterance_id': utteranceId,
    });
    _setStatus('Cancelled.');
  }

  void _onTurnEnded(String reason) {
    _recorder.stop();
    setState(() {
      _statusMessage = 'Turn ended: $reason';
    });
  }

  // ---- Room session methods ----

  void _createRoom() {
    _connection.sendJson({
      'type': 'create_room',
      'listening_language': _targetLanguage,
      'review_language': _reviewLanguage,
      'tone': _tone,
    });
    _setStatus('Creating room...');
  }

  void _joinRoom() {
    final roomId = _roomIdController.text.trim();
    final secret = _joinSecretController.text.trim();
    if (roomId.isEmpty) {
      _setStatus('Enter a room ID to join.');
      return;
    }
    _connection.sendJson({
      'type': 'join_room',
      'room_id': roomId,
      'join_secret': secret,
      'listening_language': _targetLanguage,
      'review_language': _reviewLanguage,
      'tone': _tone,
    });
    _setStatus('Joining room $roomId...');
  }

  // ---- UI ----

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Polyglot Voice'),
        actions: [
          _ConnectionIndicator(state: _connState),
        ],
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            _buildConnectionCard(),
            const SizedBox(height: 12),
            _buildRoomCard(),
            const SizedBox(height: 12),
            _buildSettingsCard(),
            const SizedBox(height: 12),
            _buildReviewSection(),
            const SizedBox(height: 12),
            _buildRecordSection(),
            const SizedBox(height: 12),
            _buildStatusArea(),
          ],
        ),
      ),
    );
  }

  Widget _buildConnectionCard() {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Server', style: TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(height: 8),
            TextField(
              controller: _urlController,
              decoration: const InputDecoration(
                labelText: 'WebSocket URL',
                hintText: 'ws://laptop-ip:8000/ws or wss://tunnel/ws',
                border: OutlineInputBorder(),
                isDense: true,
              ),
              keyboardType: TextInputType.url,
              enabled: _connState == ConnState.disconnected,
            ),
            const SizedBox(height: 8),
            TextField(
              controller: _tokenController,
              decoration: const InputDecoration(
                labelText: 'Demo access token (optional)',
                border: OutlineInputBorder(),
                isDense: true,
              ),
              obscureText: true,
              enabled: _connState == ConnState.disconnected,
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: FilledButton(
                    onPressed:
                        _connState == ConnState.disconnected ? _connect : null,
                    child: const Text('Connect'),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OutlinedButton(
                    onPressed: _connState == ConnState.connected
                        ? () => _connection.disconnect()
                        : null,
                    child: const Text('Disconnect'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildRoomCard() {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Text('Two-Phone Room',
                    style: TextStyle(fontWeight: FontWeight.bold)),
                const Spacer(),
                if (_roomBusy)
                  const Chip(
                    label: Text('Peer speaking…'),
                    visualDensity: VisualDensity.compact,
                  ),
              ],
            ),
            const SizedBox(height: 8),
            if (_joinedRoomId == null) ...[
              // Not in a room: create one or join an existing one.
              SizedBox(
                width: double.infinity,
                child: FilledButton.tonal(
                  onPressed: _connState == ConnState.connected
                      ? _createRoom
                      : null,
                  child: const Text('Create new room'),
                ),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: _roomIdController,
                decoration: const InputDecoration(
                  labelText: 'Room ID (to join)',
                  border: OutlineInputBorder(),
                  isDense: true,
                ),
                enabled: _connState == ConnState.connected,
              ),
              const SizedBox(height: 8),
              TextField(
                controller: _joinSecretController,
                decoration: const InputDecoration(
                  labelText: 'Join secret',
                  border: OutlineInputBorder(),
                  isDense: true,
                ),
                obscureText: true,
                enabled: _connState == ConnState.connected,
              ),
              const SizedBox(height: 8),
              SizedBox(
                width: double.infinity,
                child: OutlinedButton(
                  onPressed:
                      _connState == ConnState.connected ? _joinRoom : null,
                  child: const Text('Join room'),
                ),
              ),
            ] else ...[
              // In a room: show credentials/status for sharing.
              SelectableText(
                'Room ID: $_joinedRoomId',
                style: const TextStyle(
                    fontFamily: 'monospace', fontSize: 16),
              ),
              if (_sharedJoinSecret != null)
                SelectableText(
                  'Join secret: $_sharedJoinSecret',
                  style: const TextStyle(fontFamily: 'monospace'),
                ),
              if (_myParticipantId != null)
                Text('You are participant $_myParticipantId'),
              const SizedBox(height: 4),
              Row(
                children: [
                  Icon(
                    _peerInRoom ? Icons.person : Icons.person_off,
                    size: 16,
                    color: _peerInRoom ? Colors.green : Colors.grey,
                  ),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(_peerInRoom
                        ? 'Peer connected — take turns speaking.'
                        : 'Waiting for the other phone to join…'),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildSettingsCard() {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Languages & Tone',
                style: TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: DropdownButtonFormField<String>(
                    initialValue: _targetLanguage,
                    decoration: const InputDecoration(
                      labelText: 'Target',
                      border: OutlineInputBorder(),
                      isDense: true,
                    ),
                    items: kLanguages
                        .map((l) => DropdownMenuItem(value: l, child: Text(l)))
                        .toList(),
                    onChanged: (v) =>
                        setState(() => _targetLanguage = v ?? 'ja'),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: DropdownButtonFormField<String>(
                    initialValue: _reviewLanguage,
                    decoration: const InputDecoration(
                      labelText: 'Review',
                      border: OutlineInputBorder(),
                      isDense: true,
                    ),
                    items: kLanguages
                        .map((l) => DropdownMenuItem(value: l, child: Text(l)))
                        .toList(),
                    onChanged: (v) =>
                        setState(() => _reviewLanguage = v ?? 'en'),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            SegmentedButton<String>(
              segments: kTones
                  .map((t) => ButtonSegment(value: t, label: Text(t)))
                  .toList(),
              selected: {_tone},
              onSelectionChanged: (selection) =>
                  setState(() => _tone = selection.first),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildReviewSection() {
    final review = _turn.pendingReview;
    final showCountdown = _phase == TurnPhase.review && _turn.countdownActive;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Review', style: TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(height: 8),
            if (_transcript == null)
              const Text('No turn yet. Press record and speak.')
            else ...[
              _buildLabeledText('You said', _transcript!),
              _buildLabeledText(
                  'Translation ($_targetLanguage)', _translated ?? ''),
              _buildLabeledText(
                  'Back-translation (check)', _backTranslation ?? ''),
              const SizedBox(height: 12),
              if (showCountdown && review != null)
                Center(
                  child: Column(
                    children: [
                      CountdownRing(
                        totalMs: review.verificationWindowMs,
                        onExpired: _approve,
                        onCancel: _cancelTurn,
                      ),
                      const SizedBox(height: 8),
                      const Text('Tap ring to cancel'),
                    ],
                  ),
                )
              else
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Expanded(
                      child: FilledButton.icon(
                        onPressed: _phase == TurnPhase.review ? _approve : null,
                        icon: const Icon(Icons.check),
                        label: const Text('Approve'),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed:
                            _phase == TurnPhase.review ? _cancelTurn : null,
                        icon: const Icon(Icons.close),
                        label: const Text('Cancel'),
                      ),
                    ),
                  ],
                ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildLabeledText(String label, String value) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: const TextStyle(fontSize: 12, color: Colors.grey)),
          Text(value, style: const TextStyle(fontSize: 16)),
        ],
      ),
    );
  }

  Widget _buildRecordSection() {
    final canRecord = _connState == ConnState.connected && !_turn.isTurnActive;
    final recording = _phase == TurnPhase.listening;
    return Center(
      child: Column(
        children: [
          FloatingActionButton.large(
            onPressed: canRecord
                ? _startRecording
                : (recording ? _stopRecording : null),
            backgroundColor:
                recording ? Theme.of(context).colorScheme.error : null,
            child: Icon(recording ? Icons.stop : Icons.mic),
          ),
          const SizedBox(height: 4),
          Text(recording ? 'Listening... (tap to stop)' : 'Press to speak'),
        ],
      ),
    );
  }

  Widget _buildStatusArea() {
    final busy =
        _phase == TurnPhase.processing || _phase == TurnPhase.synthesizing;
    return Column(
      children: [
        if (busy) const LinearProgressIndicator(),
        if (_playbackFailed)
          const ListTile(
            leading: Icon(Icons.error, color: Colors.red),
            title: Text('Audio playback failed'),
          ),
        if (_statusMessage != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              _statusMessage!,
              style: TextStyle(
                color: _statusMessage!.startsWith('Error') ||
                        _statusMessage!.contains('denied') ||
                        _statusMessage!.contains('failed')
                    ? Colors.red
                    : Colors.black54,
              ),
              textAlign: TextAlign.center,
            ),
          ),
      ],
    );
  }
}

class _ConnectionIndicator extends StatelessWidget {
  const _ConnectionIndicator({required this.state});

  final ConnState state;

  @override
  Widget build(BuildContext context) {
    final (color, label) = switch (state) {
      ConnState.connected => (Colors.green, 'Connected'),
      ConnState.connecting => (Colors.orange, '...'),
      ConnState.disconnected => (Colors.red, 'Offline'),
    };
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.circle, color: color, size: 12),
          const SizedBox(width: 6),
          Text(label, style: const TextStyle(fontSize: 12)),
        ],
      ),
    );
  }
}
