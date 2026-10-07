import 'package:flutter/material.dart';

import 'screens/translation_screen.dart';

void main() {
  runApp(const PolyglotVoiceApp());
}

class PolyglotVoiceApp extends StatelessWidget {
  const PolyglotVoiceApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Polyglot Voice',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.deepPurple),
        useMaterial3: true,
      ),
      home: const TranslationScreen(),
    );
  }
}
