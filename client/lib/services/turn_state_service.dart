import 'dart:async';

/// Local alias so this file stays pure Dart (no Flutter imports),
/// which keeps it unit-testable without a Flutter binding.
typedef VoidCallback = void Function();

/// Lifecycle of a single translation turn on the client.
///
/// Mirrors the backend turn state machine:
/// idle -> listening -> processing -> review -> (synthesizing -> complete)
///                                          \-> (cancelled -> complete)
enum TurnPhase {
  idle,
  listening,
  processing,
  review,
  synthesizing,
  complete,
}

/// Result info for a review step (translation + back-translation).
class ReviewInfo {
  ReviewInfo({
    required this.utteranceId,
    required this.transcript,
    required this.translatedText,
    required this.backTranslation,
    required this.targetLanguage,
    required this.verificationWindowMs,
  });

  final String utteranceId;
  final String transcript;
  final String translatedText;
  final String backTranslation;
  final String targetLanguage;
  final int verificationWindowMs;
}

/// Callbacks invoked by [TurnController] on state changes.
class TurnCallbacks {
  TurnCallbacks({
    this.onPhaseChanged,
    this.onReviewPresented,
    this.onTurnEnded,
    this.onCountdownExpired,
    this.onError,
  });

  final void Function(TurnPhase phase)? onPhaseChanged;
  final void Function(ReviewInfo review)? onReviewPresented;
  final void Function(String reason)? onTurnEnded;

  /// Fired when the verification countdown reaches zero without a decision.
  /// The UI treats this as implicit approval (the speaker can cancel during
  /// the window; expiry means "go ahead").
  final VoidCallback? onCountdownExpired;
  final void Function(String code, String message)? onError;
}

/// Tracks a single turn's lifecycle and owns the verification countdown.
///
/// Protocol rules enforced here:
/// - Elapsed time is measured with a monotonic [Stopwatch], not by trusting
///   timer tick cadence.
/// - An approval is sent at most once per utterance.
/// - Late cancels cannot resurrect a cancelled/completed turn.
/// - Countdown is cancelled on backgrounding/disconnect via [cancelCountdown].
class TurnController {
  TurnController({required this.callbacks});

  final TurnCallbacks callbacks;

  TurnPhase _phase = TurnPhase.idle;
  ReviewInfo? _pendingReview;
  bool _approvalSent = false;
  bool _countdownActive = false;
  final Stopwatch _countdownStopwatch = Stopwatch();
  Timer? _countdownTimer;
  Duration _countdownDuration = Duration.zero;

  TurnPhase get phase => _phase;
  ReviewInfo? get pendingReview => _pendingReview;
  bool get countdownActive => _countdownActive;
  bool get isTurnActive =>
      _phase != TurnPhase.idle && _phase != TurnPhase.complete;
  bool get awaitingDecision => _phase == TurnPhase.review && !_approvalSent;

  /// Remaining countdown time in milliseconds (0 when not active).
  int get countdownRemainingMs {
    if (!_countdownActive) return 0;
    final remaining = _countdownDuration.inMilliseconds -
        _countdownStopwatch.elapsedMilliseconds;
    return remaining < 0 ? 0 : remaining;
  }

  void startTurn() {
    if (isTurnActive) {
      callbacks.onError?.call('server_busy', 'Another turn is still active.');
      return;
    }
    _approvalSent = false;
    _pendingReview = null;
    _setPhase(TurnPhase.listening);
  }

  /// Server signalled audio is being processed.
  void onProcessing() {
    if (!isTurnActive) return;
    _setPhase(TurnPhase.processing);
  }

  /// Server presented translation + back-translation for review.
  void onReview(ReviewInfo review) {
    if (!isTurnActive) return;
    _pendingReview = review;
    _setPhase(TurnPhase.review);
    callbacks.onReviewPresented?.call(review);
  }

  /// Starts the verification countdown.
  ///
  /// MUST be called only after the review has been presented to the user
  /// (presentation acknowledgement). Uses monotonic elapsed time for expiry;
  /// the periodic timer only drives repaints.
  void startCountdown() {
    final review = _pendingReview;
    if (review == null || !awaitingDecision) return;
    _countdownDuration = Duration(milliseconds: review.verificationWindowMs);
    _countdownStopwatch
      ..reset()
      ..start();
    _countdownActive = true;
    _countdownTimer?.cancel();
    _countdownTimer = Timer.periodic(
      const Duration(milliseconds: 50),
      (_) => _tickCountdown(),
    );
  }

  void _tickCountdown() {
    if (!_countdownActive) return;
    if (_countdownStopwatch.elapsed >= _countdownDuration) {
      cancelCountdown();
      callbacks.onCountdownExpired?.call();
    }
  }

  /// Stops the countdown without any decision (backgrounding/disconnect).
  void cancelCountdown() {
    _countdownTimer?.cancel();
    _countdownTimer = null;
    _countdownStopwatch.stop();
    _countdownActive = false;
  }

  /// Marks approval as sent (at most once per turn).
  /// Returns false if the turn is not in review or already approved.
  bool markApprovalSent() {
    if (_phase != TurnPhase.review || _approvalSent) return false;
    _approvalSent = true;
    cancelCountdown();
    _setPhase(TurnPhase.synthesizing);
    return true;
  }

  /// Marks the turn as cancelled.
  /// Returns false if an approval was already sent or the turn is finished.
  bool markCancelled() {
    if (_approvalSent) return false;
    if (_phase == TurnPhase.idle || _phase == TurnPhase.complete) {
      return false;
    }
    cancelCountdown();
    _setPhase(TurnPhase.complete);
    _pendingReview = null;
    return true;
  }

  /// Server signalled the turn is over for any reason.
  void onTurnEnded(String reason) {
    cancelCountdown();
    _setPhase(TurnPhase.complete);
    _pendingReview = null;
    callbacks.onTurnEnded?.call(reason);
  }

  void dispose() {
    cancelCountdown();
    _phase = TurnPhase.idle;
    _pendingReview = null;
  }

  void _setPhase(TurnPhase newPhase) {
    if (_phase == newPhase) return;
    _phase = newPhase;
    callbacks.onPhaseChanged?.call(newPhase);
  }
}
