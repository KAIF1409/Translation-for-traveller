import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:polyglot_voice_client/widgets/countdown_ring.dart';

/// Deterministic stopwatch driven by the test (real Stopwatch ignores the
/// fake clock that widget tests pump).
class FakeStopwatch implements Stopwatch {
  int elapsedMs = 0;
  bool running = false;

  @override
  bool get isRunning => running;

  @override
  Duration get elapsed => Duration(milliseconds: elapsedMs);

  @override
  int get elapsedMilliseconds => elapsedMs;

  @override
  int get elapsedMicroseconds => elapsedMs * 1000;

  @override
  int get elapsedTicks => elapsedMs;

  @override
  int get frequency => 1000;

  @override
  void start() => running = true;

  @override
  void stop() => running = false;

  @override
  void reset() => elapsedMs = 0;
}

void main() {
  Future<void> pumpRing(
    WidgetTester tester, {
    required int totalMs,
    required FakeStopwatch stopwatch,
    VoidCallback? onExpired,
    VoidCallback? onCancel,
  }) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Center(
            child: CountdownRing(
              totalMs: totalMs,
              onExpired: onExpired ?? () {},
              onCancel: onCancel,
              stopwatchFactory: () => stopwatch,
            ),
          ),
        ),
      ),
    );
    // Let the first periodic tick run.
    await tester.pump(const Duration(milliseconds: 50));
  }

  testWidgets('renders and shows remaining seconds', (tester) async {
    final sw = FakeStopwatch();
    await pumpRing(tester, totalMs: 5000, stopwatch: sw);
    // 5000ms remaining -> ceil(5000/1000) = 5
    expect(find.text('5'), findsOneWidget);
    expect(find.byType(CountdownRing), findsOneWidget);
  });

  testWidgets('fires onExpired exactly once after the window elapses',
      (tester) async {
    final sw = FakeStopwatch();
    var expiredCount = 0;
    await pumpRing(
      tester,
      totalMs: 300,
      stopwatch: sw,
      onExpired: () => expiredCount++,
    );
    expect(expiredCount, 0);

    // Advance monotonic time past the window, then pump a tick.
    sw.elapsedMs = 400;
    await tester.pump(const Duration(milliseconds: 50));
    expect(expiredCount, 1);

    // More pumping must not fire it again.
    sw.elapsedMs = 1000;
    await tester.pump(const Duration(milliseconds: 50));
    await tester.pump(const Duration(milliseconds: 50));
    expect(expiredCount, 1);
  });

  testWidgets('does not fire before the window elapses', (tester) async {
    final sw = FakeStopwatch();
    var expiredCount = 0;
    await pumpRing(
      tester,
      totalMs: 2000,
      stopwatch: sw,
      onExpired: () => expiredCount++,
    );
    sw.elapsedMs = 500;
    await tester.pump(const Duration(milliseconds: 50));
    expect(expiredCount, 0);
  });

  testWidgets('tap invokes onCancel', (tester) async {
    final sw = FakeStopwatch();
    var cancelled = false;
    await pumpRing(
      tester,
      totalMs: 5000,
      stopwatch: sw,
      onCancel: () => cancelled = true,
    );
    await tester.tap(find.byType(CountdownRing));
    await tester.pump();
    expect(cancelled, isTrue);
  });

  testWidgets('countdown progresses (ring value decreases)', (tester) async {
    final sw = FakeStopwatch();
    await pumpRing(tester, totalMs: 10000, stopwatch: sw);
    expect(find.text('10'), findsOneWidget);
    sw.elapsedMs = 1500;
    await tester.pump(const Duration(milliseconds: 50));
    expect(find.text('9'), findsOneWidget);
  });
}
