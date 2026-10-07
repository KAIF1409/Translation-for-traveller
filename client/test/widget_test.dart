// Smoke test: the app builds and shows the main screen.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:polyglot_voice_client/main.dart';

void main() {
  testWidgets('app renders the translation screen',
      (WidgetTester tester) async {
    await tester.pumpWidget(const PolyglotVoiceApp());
    await tester.pump();

    expect(find.text('Polyglot Voice'), findsOneWidget);
    expect(find.text('Server'), findsOneWidget);
    expect(find.text('Two-Phone Room'), findsOneWidget);
    // The ListView below the fold is lazy: scroll to the review section
    // before asserting it rendered.
    await tester.scrollUntilVisible(
      find.text('No turn yet. Press record and speak.'),
      200.0,
      scrollable: find.byType(Scrollable).first,
    );
    // "Review" appears as section header and language dropdown label.
    expect(find.text('Review'), findsWidgets);
    expect(find.text('No turn yet. Press record and speak.'), findsOneWidget);
  });
}
