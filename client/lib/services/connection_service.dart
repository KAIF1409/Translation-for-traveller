import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:web_socket_channel/web_socket_channel.dart';

/// Connection states for the WebSocket link to the backend.
enum ConnState { disconnected, connecting, connected }

/// Manages the WebSocket connection to the Polyglot Voice backend.
///
/// Responsibilities:
/// - Connect/disconnect lifecycle with status reporting.
/// - Automatic reconnection with exponential backoff + jitter after an
///   unexpected drop (bounded attempts; manual reconnect always available).
/// - Serialize outbound JSON control messages.
/// - Dispatch inbound JSON messages to [onJsonMessage] and binary WAV
///   payloads to [onBinaryMessage].
///
/// API keys for cloud providers never live here; only an optional demo
/// access token supplied by the user is appended as a query parameter.
class ConnectionService {
  ConnectionService({
    required this.onJsonMessage,
    required this.onBinaryMessage,
    required this.onStateChange,
    this.maxReconnectAttempts = 5,
  });

  final void Function(Map<String, dynamic> json) onJsonMessage;
  final void Function(List<int> bytes) onBinaryMessage;
  final void Function(ConnState state) onStateChange;
  final int maxReconnectAttempts;

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _subscription;
  ConnState _state = ConnState.disconnected;
  Timer? _reconnectTimer;
  int _reconnectAttempt = 0;
  bool _userDisconnected = false;
  String _lastUrl = '';
  String? _lastToken;

  ConnState get state => _state;
  bool get isReconnecting => _reconnectTimer != null;

  /// Connects to [serverUrl]. Accepts ws:// or wss:// (ngrok tunnels use wss).
  Future<void> connect(String serverUrl, {String? accessToken}) async {
    if (_state != ConnState.disconnected) {
      await disconnect();
    }
    var url = serverUrl.trim();
    if (url.isEmpty) {
      throw ArgumentError('Server URL is empty.');
    }
    if (!url.startsWith('ws://') && !url.startsWith('wss://')) {
      throw ArgumentError('Server URL must start with ws:// or wss://');
    }
    // Remember credentials for automatic reconnection.
    _lastUrl = url;
    _lastToken = accessToken;
    _userDisconnected = false;
    _reconnectAttempt = 0;
    _cancelReconnectTimer();
    await _doConnect(url, accessToken);
  }

  Future<void> _doConnect(String url, String? accessToken) async {
    if (accessToken != null && accessToken.trim().isNotEmpty) {
      final separator = url.contains('?') ? '&' : '?';
      url = '$url${separator}token=${Uri.encodeComponent(accessToken.trim())}';
    }

    _setState(ConnState.connecting);
    try {
      final channel = WebSocketChannel.connect(Uri.parse(url));
      _channel = channel;
      // readyFuture throws if the connection fails (e.g. bad host).
      await channel.ready;
      _setState(ConnState.connected);
      _reconnectAttempt = 0;
      _subscription = channel.stream.listen(
        _onData,
        onDone: () => _onConnectionLost(),
        onError: (Object e) => _onConnectionLost(),
      );
    } catch (e) {
      _setState(ConnState.disconnected);
      rethrow;
    }
  }

  /// Unexpected drop (not user-initiated): schedule a reconnect with
  /// exponential backoff and jitter, up to [maxReconnectAttempts].
  void _onConnectionLost() {
    _subscription = null;
    _channel = null;
    _setState(ConnState.disconnected);
    if (_userDisconnected) return;
    if (_reconnectAttempt >= maxReconnectAttempts) {
      onJsonMessage({
        'type': 'local_error',
        'message': 'Connection lost. Reconnect manually.',
      });
      return;
    }
    _reconnectAttempt++;
    // Exponential backoff with full jitter: 1s, 2s, 4s, 8s... capped at 15s.
    final base =
        (1 << (_reconnectAttempt - 1)).clamp(1, 15); // seconds, powers of 2
    final delayMs = (base * 1000 * (0.5 + Random().nextDouble())).round();
    _reconnectTimer = Timer(Duration(milliseconds: delayMs), () async {
      _reconnectTimer = null;
      if (_userDisconnected || _lastUrl.isEmpty) return;
      try {
        await _doConnect(_lastUrl, _lastToken);
      } catch (_) {
        // connect() rethrows after setting state; the next attempt is
        // scheduled by the failure path below.
        _onConnectionLost();
      }
    });
  }

  void _cancelReconnectTimer() {
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
  }

  void _onData(dynamic data) {
    if (data is List<int>) {
      onBinaryMessage(data);
    } else if (data is String) {
      try {
        final decoded = jsonDecode(data);
        if (decoded is Map<String, dynamic>) {
          onJsonMessage(decoded);
        }
      } on FormatException {
        // Ignore malformed JSON frames.
      }
    }
  }

  /// Sends a JSON control message.
  void sendJson(Map<String, dynamic> json) {
    final channel = _channel;
    if (channel == null || _state != ConnState.connected) return;
    channel.sink.add(jsonEncode(json));
  }

  /// Sends a raw binary audio frame.
  void sendBinary(List<int> bytes) {
    final channel = _channel;
    if (channel == null || _state != ConnState.connected) return;
    channel.sink.add(bytes);
  }

  /// Closes the connection (if any). User-initiated: no auto-reconnect.
  Future<void> disconnect() async {
    _userDisconnected = true;
    _cancelReconnectTimer();
    await _subscription?.cancel();
    _subscription = null;
    _channel?.sink.close();
    _channel = null;
    _setState(ConnState.disconnected);
  }

  void _setState(ConnState newState) {
    if (_state == newState) return;
    _state = newState;
    onStateChange(newState);
  }
}
