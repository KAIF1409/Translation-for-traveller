import 'dart:async';

import 'package:flutter/material.dart';

/// A circular progress ring counting down the verification window.
///
/// Time source: the ring owns a monotonic [Stopwatch]; expiry is determined
/// by real elapsed time, never by counting timer ticks (which can drift or
/// be delayed). The periodic timer only drives repaints.
///
/// Tapping the ring cancels. When the countdown reaches zero exactly once,
/// [onExpired] fires.
class CountdownRing extends StatefulWidget {
  const CountdownRing({
    super.key,
    required this.totalMs,
    required this.onExpired,
    this.onCancel,
    this.size = 96,
    this.strokeWidth = 8,
    this.color,
    this.stopwatchFactory = Stopwatch.new,
  });

  /// Total countdown duration in milliseconds.
  final int totalMs;

  /// Fired exactly once when the countdown reaches zero (auto-approve).
  final VoidCallback onExpired;

  /// Invoked when the user taps to cancel.
  final VoidCallback? onCancel;

  final double size;
  final double strokeWidth;
  final Color? color;

  /// Creates the monotonic time source. Overridable in tests to drive
  /// elapsed time deterministically (real Stopwatch ignores fake clocks).
  final Stopwatch Function() stopwatchFactory;

  @override
  State<CountdownRing> createState() => _CountdownRingState();
}

class _CountdownRingState extends State<CountdownRing> {
  late final Stopwatch _stopwatch = widget.stopwatchFactory();
  Timer? _timer;
  bool _expired = false;

  @override
  void initState() {
    super.initState();
    _stopwatch.start();
    _timer = Timer.periodic(const Duration(milliseconds: 50), (_) => _tick());
  }

  void _tick() {
    if (!mounted || _expired) return;
    // Trust elapsed monotonic time, not tick count.
    if (_stopwatch.elapsedMilliseconds >= widget.totalMs) {
      _expired = true;
      _stopwatch.stop();
      _timer?.cancel();
      _timer = null;
      widget.onExpired();
    }
    setState(() {}); // repaint with fresh remaining
  }

  @override
  void dispose() {
    _timer?.cancel();
    _timer = null;
    _stopwatch.stop();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final total = widget.totalMs <= 0 ? 1 : widget.totalMs;
    final remaining = (total - _stopwatch.elapsedMilliseconds).clamp(0, total);
    final fraction = remaining / total;
    final seconds = (remaining / 1000).ceil().clamp(0, 999);

    return GestureDetector(
      onTap: widget.onCancel,
      child: SizedBox(
        width: widget.size,
        height: widget.size,
        child: Stack(
          alignment: Alignment.center,
          children: [
            CustomPaint(
              size: Size(widget.size, widget.size),
              painter: _RingPainter(
                fraction: fraction,
                strokeWidth: widget.strokeWidth,
                color: widget.color ?? Theme.of(context).colorScheme.secondary,
              ),
            ),
            Text('$seconds', style: Theme.of(context).textTheme.headlineMedium),
          ],
        ),
      ),
    );
  }
}

class _RingPainter extends CustomPainter {
  _RingPainter({
    required this.fraction,
    required this.strokeWidth,
    required this.color,
  });

  final double fraction;
  final double strokeWidth;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = (size.shortestSide - strokeWidth) / 2;

    final trackPaint = Paint()
      ..color = color.withValues(alpha: 0.15)
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth;

    final arcPaint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth
      ..strokeCap = StrokeCap.round;

    // Track
    canvas.drawCircle(center, radius, trackPaint);

    // Progress arc sweeps from the top (12 o'clock).
    final sweep = 2 * 3.141592653589793 * fraction.clamp(0.0, 1.0);
    final startAngle = -3.141592653589793 / 2;
    final rect = Rect.fromCircle(center: center, radius: radius);
    canvas.drawArc(rect, startAngle, sweep, false, arcPaint);
  }

  @override
  bool shouldRepaint(covariant _RingPainter oldDelegate) {
    return oldDelegate.fraction != fraction ||
        oldDelegate.color != color ||
        oldDelegate.strokeWidth != strokeWidth;
  }
}
