import 'package:flutter_test/flutter_test.dart';
import 'package:polyglot_voice_client/services/turn_state_service.dart';

ReviewInfo makeReview({int windowMs = 800}) => ReviewInfo(
      utteranceId: 'u1',
      transcript: 'hello',
      translatedText: 'konnichiwa',
      backTranslation: 'hello',
      targetLanguage: 'ja',
      verificationWindowMs: windowMs,
    );

void main() {
  late TurnController controller;
  late List<TurnPhase> phases;
  late List<String> endedReasons;
  late int countdownExpirations;
  late List<(String, String)> errors;

  setUp(() {
    phases = [];
    endedReasons = [];
    countdownExpirations = 0;
    errors = [];
    controller = TurnController(
      callbacks: TurnCallbacks(
        onPhaseChanged: (p) => phases.add(p),
        onTurnEnded: (reason) => endedReasons.add(reason),
        onCountdownExpired: () => countdownExpirations++,
        onError: (code, message) => errors.add((code, message)),
      ),
    );
  });

  group('state transitions', () {
    test('full lifecycle', () {
      controller.startTurn();
      expect(controller.phase, TurnPhase.listening);
      expect(controller.isTurnActive, isTrue);

      controller.onProcessing();
      expect(controller.phase, TurnPhase.processing);

      controller.onReview(makeReview());
      expect(controller.phase, TurnPhase.review);
      expect(controller.awaitingDecision, isTrue);

      expect(controller.markApprovalSent(), isTrue);
      expect(controller.phase, TurnPhase.synthesizing);

      controller.onTurnEnded('approved');
      expect(controller.phase, TurnPhase.complete);
      expect(controller.isTurnActive, isFalse);
    });

    test('new turn while active is rejected with error', () {
      controller.startTurn();
      controller.startTurn();
      expect(errors, hasLength(1));
      expect(errors.first.$1, 'server_busy');
    });
  });

  group('approval rules', () {
    test('approval sent at most once', () {
      controller.startTurn();
      controller.onReview(makeReview());
      expect(controller.markApprovalSent(), isTrue);
      expect(controller.markApprovalSent(), isFalse);
    });

    test('approval rejected outside review phase', () {
      controller.startTurn();
      expect(controller.markApprovalSent(), isFalse);
      controller.onProcessing();
      expect(controller.markApprovalSent(), isFalse);
    });

    test('late cancel cannot resurrect an approved turn', () {
      controller.startTurn();
      controller.onReview(makeReview());
      controller.markApprovalSent();
      expect(controller.markCancelled(), isFalse);
      expect(controller.phase, TurnPhase.synthesizing);
    });

    test('cancel during review completes the turn', () {
      controller.startTurn();
      controller.onReview(makeReview());
      expect(controller.markCancelled(), isTrue);
      expect(controller.phase, TurnPhase.complete);
      expect(controller.isTurnActive, isFalse);
    });

    test('cancel while idle is a no-op', () {
      expect(controller.markCancelled(), isFalse);
    });
  });

  group('countdown', () {
    test('does not start without a pending review', () {
      controller.startTurn();
      controller.startCountdown();
      expect(controller.countdownActive, isFalse);
    });

    test('expires via monotonic elapsed time, exactly once', () async {
      controller.startTurn();
      controller.onReview(makeReview(windowMs: 120));
      controller.startCountdown();
      expect(controller.countdownActive, isTrue);

      await Future<void>.delayed(const Duration(milliseconds: 250));
      expect(controller.countdownActive, isFalse);
      expect(countdownExpirations, 1);
      // Still in review phase; the caller decides what expiry means.
      expect(controller.phase, TurnPhase.review);
    });

    test('cancelCountdown stops expiry without firing it', () async {
      controller.startTurn();
      controller.onReview(makeReview(windowMs: 120));
      controller.startCountdown();
      controller.cancelCountdown();
      await Future<void>.delayed(const Duration(milliseconds: 250));
      expect(countdownExpirations, 0);
      expect(controller.countdownActive, isFalse);
    });

    test('approving cancels a running countdown', () async {
      controller.startTurn();
      controller.onReview(makeReview(windowMs: 5000));
      controller.startCountdown();
      controller.markApprovalSent();
      expect(controller.countdownActive, isFalse);
      await Future<void>.delayed(const Duration(milliseconds: 60));
      expect(countdownExpirations, 0);
    });

    test('remaining time decreases monotonically', () async {
      controller.startTurn();
      controller.onReview(makeReview(windowMs: 1000));
      controller.startCountdown();
      final first = controller.countdownRemainingMs;
      await Future<void>.delayed(const Duration(milliseconds: 60));
      final second = controller.countdownRemainingMs;
      expect(second, lessThanOrEqualTo(first));
      expect(second, lessThan(1000));
      controller.cancelCountdown();
    });
  });

  group('turn end handling', () {
    test('onTurnEnded completes the turn from any active phase', () {
      controller.startTurn();
      controller.onTurnEnded('no_speech');
      expect(controller.phase, TurnPhase.complete);

      controller.startTurn();
      controller.onTurnEnded('cancelled');
      expect(controller.phase, TurnPhase.complete);
    });

    test('review callback receives review info', () {
      ReviewInfo? received;
      final c = TurnController(
        callbacks: TurnCallbacks(
          onReviewPresented: (r) => received = r,
        ),
      );
      c.startTurn();
      c.onReview(makeReview());
      expect(received, isNotNull);
      expect(received!.utteranceId, 'u1');
    });

    test('server review during inactive turn is ignored', () {
      controller.onReview(makeReview());
      expect(controller.phase, TurnPhase.idle);
    });
  });
}
