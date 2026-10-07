"""WebSocket handler for the translation pipeline.

Implements: audio in → VAD → STT → LLM translate+verify → TTS → audio out.
Runs on a single client connection with one active turn at a time.
"""
import asyncio
import json
import logging
import secrets
import time
import uuid
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from app.config import settings
from app.services.ws_protocol import (
    MessageType, StartRequest, ApproveRequest, CancelRequest,
    ReadyResponse, ReviewResponse, ProcessingResponse, ErrorResponse,
    TurnEndResponse, ErrorCode,
    CreateRoomRequest, JoinRoomRequest, UpdatePrefsRequest,
    RoomCreatedResponse, JoinedResponse, PeerJoinedResponse, PeerLeftResponse,
    BusyResponse, PrefsUpdatedResponse
)
from app.services.turn_state import TurnManager, TurnState
from app.services.session_manager import session_manager, Participant, Room, RoomError
from app.services.vad_service import VADService
from app.services.stt_router import STTRouter
from app.services.llm_orchestrator import LLMOrchestrator
from app.services.tts_router import TTSRouter

logger = logging.getLogger(__name__)


class WebSocketHandler:
    """Handles a single WebSocket connection through the translation pipeline."""
    
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.turn_manager = TurnManager()
        self.session_id = id(websocket)
        
        # Initialize services
        self.vad_service = VADService()
        self.stt_router = STTRouter()
        self.llm_orchestrator = LLMOrchestrator()
        self.tts_router = TTSRouter()
        
        # Pipeline tasks (for cancellation)
        self._current_task: Optional[asyncio.Task] = None
        
        # Room mode state (Phase 7); None = single-client mode
        self.room: Optional[Room] = None
        self.participant: Optional[Participant] = None
        self._playback_timeout_task: Optional[asyncio.Task] = None
        
        # Audio settings
        self.sample_rate = 16000
        self.max_audio_seconds = 15
        self.max_audio_bytes = self.sample_rate * 2 * self.max_audio_seconds  # 16-bit PCM
        
    async def handle(self):
        """Main WebSocket handling loop."""
        await self.websocket.accept()
        logger.info(f"WebSocket connected: session {self.session_id}")
        
        try:
            # Send ready message
            await self._send_ready()
            
            # Message receive loop
            await self._receive_loop()
                
        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected: session {self.session_id}")
        except Exception as e:
            logger.error(f"WebSocket error: {e}", exc_info=True)
        finally:
            await self._cleanup()
    
    async def _receive_loop(self):
        """Main loop for receiving messages."""
        while True:
            # receive() must be isolated from dispatch: once the peer has
            # disconnected the stream is dead and further receive() calls
            # raise. Treat any receive() failure as fatal and exit cleanly
            # instead of spinning in a log-error loop.
            try:
                data = await self.websocket.receive()
            except asyncio.CancelledError:
                break
            except WebSocketDisconnect:
                raise
            except Exception as e:
                # e.g. "Cannot call receive once a disconnect message has
                # been received" — the connection is gone.
                logger.info(f"Receive stream closed for session {self.session_id}: {e}")
                break

            if data.get("type") == "websocket.disconnect":
                break

            # Dispatch errors are per-message: report and keep serving.
            try:
                if "bytes" in data and data["bytes"] is not None:
                    # Binary audio data
                    await self._handle_audio(data["bytes"])
                elif "text" in data and data["text"] is not None:
                    # JSON control message
                    await self._handle_control_message(data["text"])
                else:
                    logger.warning(f"Unknown message format from session {self.session_id}")
            except asyncio.CancelledError:
                raise
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.error(f"Error handling message from session {self.session_id}: {e}")
                await self._send_error(ErrorCode.INVALID_MESSAGE, f"Failed to process message: {str(e)}")
    
    async def _handle_control_message(self, text: str):
        """Handle JSON control messages."""
        try:
            data = json.loads(text)
            msg_type = data.get("type", "")
            
            if msg_type == MessageType.START:
                await self._handle_start(StartRequest(**data))
            elif msg_type == MessageType.APPROVE:
                await self._handle_approve(ApproveRequest(**data))
            elif msg_type == MessageType.CANCEL:
                await self._handle_cancel(CancelRequest(**data))
            elif msg_type == MessageType.ACK:
                logger.debug(f"ACK received: {data.get('message_type')}")
            elif msg_type == MessageType.CREATE_ROOM:
                await self._handle_create_room(CreateRoomRequest(**data))
            elif msg_type == MessageType.JOIN_ROOM:
                await self._handle_join_room(JoinRoomRequest(**data))
            elif msg_type == MessageType.UPDATE_PREFS:
                await self._handle_update_prefs(UpdatePrefsRequest(**data))
            elif msg_type == MessageType.PLAYBACK_COMPLETE:
                await self._handle_playback_complete()
            elif msg_type == MessageType.PEER_LEFT:
                await self._handle_peer_left_message(data)
            else:
                await self._send_error(ErrorCode.INVALID_MESSAGE, f"Unknown message type: {msg_type}")
                
        except json.JSONDecodeError:
            await self._send_error(ErrorCode.INVALID_MESSAGE, "Invalid JSON")
        except Exception as e:
            await self._send_error(ErrorCode.INVALID_MESSAGE, f"Message validation error: {str(e)}")
    
    async def _handle_start(self, request: StartRequest):
        """Handle start turn request."""
        try:
            # Check if there's already an active turn
            if self.turn_manager.has_active_turn:
                await self._send_error(ErrorCode.SERVER_BUSY, "Another turn is still active")
                return
            
            # Create new turn
            target_language = request.target_language
            tone = request.tone
            review_language = request.review_language

            # ---- Room mode: identity is bound to this connection at join
            # time. Never trust client-supplied IDs; if the client re-sends
            # credentials, verify they match, but empty credentials are
            # accepted because the connection is already authenticated. ----
            if self.room and self.participant:
                if (request.participant_id
                        and request.participant_id != self.participant.participant_id):
                    await self._send_error(ErrorCode.NOT_AUTHENTICATED,
                                           "Participant mismatch")
                    return
                acquired = await self.room.acquire_turn(self.participant.participant_id)
                if not acquired:
                    await self._send_message(BusyResponse())
                    return
                peer = self.room.get_peer(self.participant.participant_id)
                if peer is None:
                    self.room.release_turn(self.participant.participant_id)
                    await self._send_error(ErrorCode.ROOM_NOT_FOUND, "No peer in room")
                    return
                # Half-duplex routing policy:
                # translate into the PEER's listening language, back-translate
                # into the SPEAKER's review language, use the speaker's tone.
                target_language = peer.listening_language
                review_language = self.participant.review_language
                tone = self.participant.tone

            turn = self.turn_manager.create_turn(
                target_language=target_language,
                tone=tone,
                review_language=review_language
            )
            
            # Reset VAD state for new turn
            self.vad_service.reset()
            
            # Transition to listening
            turn.transition_to(TurnState.LISTENING)
            
            logger.info(f"Turn {turn.utterance_id}: started listening")
            
        except Exception as e:
            logger.error(f"Error starting turn: {e}")
            await self._send_error(ErrorCode.SERVER_BUSY, str(e))
    
    async def _handle_audio(self, audio_bytes: bytes):
        """Handle incoming audio chunks."""
        turn = self.turn_manager.current_turn
        
        if not turn or not turn.is_active:
            # No active turn, ignore audio
            return
        
        if turn.state != TurnState.LISTENING:
            # Not in listening state, ignore
            return
        
        # Check audio buffer size limit
        if len(turn.audio_buffer) + len(audio_bytes) > self.max_audio_bytes:
            await self._send_error(ErrorCode.INVALID_AUDIO, f"Audio exceeds maximum duration ({self.max_audio_seconds}s)")
            return
        
        # Process through VAD
        speech_segments = self.vad_service.process_chunk(audio_bytes)
        
        # Append speech to buffer (fall back to raw audio if VAD returns no segments)
        if speech_segments:
            for segment in speech_segments:
                turn.audio_buffer.extend(segment)
        else:
            # VAD did not segment; accumulate raw audio so it can still be processed
            turn.audio_buffer.extend(audio_bytes)
        
        # Check for end of speech (silence detected)
        if self.vad_service.is_speech_ended():
            if len(turn.audio_buffer) > 0:
                # Start processing in background
                await self._start_processing(turn)
            else:
                # No speech detected
                logger.info(f"Turn {turn.utterance_id}: no speech detected")
                self._release_room_turn()
                turn.transition_to(TurnState.COMPLETE)
                await self._send_turn_end("no_speech")
    
    async def _start_processing(self, turn):
        """Start the processing pipeline."""
        # Cancel any existing processing task
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
        
        # Transition to processing and send processing message IMMEDIATELY
        # (before the background task starts, so the client receives it right away)
        turn.transition_to(TurnState.PROCESSING)
        await self._send_message(ProcessingResponse(message="Processing audio..."))
        
        # Start new processing task
        self._current_task = asyncio.create_task(self._process_turn(turn))
        # Never let a background-task exception vanish silently: log it.
        self._current_task.add_done_callback(self._log_task_crash)
    
    def _log_task_crash(self, task: asyncio.Task):
        """Log unretrieved exceptions from the processing task."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                f"Processing task crashed: {exc}", exc_info=exc
            )
    
    async def _process_turn(self, turn):
        """Run the full translation pipeline."""
        t_pipeline_start = time.perf_counter()
        try:
            # Step 1: Speech-to-Text
            logger.info(f"Turn {turn.utterance_id}: starting STT")
            audio_data = bytes(turn.audio_buffer)

            t0 = time.perf_counter()
            stt_result = await self.stt_router.transcribe(
                audio_bytes=audio_data,
                sample_rate=self.sample_rate
            )
            stt_ms = (time.perf_counter() - t0) * 1000.0
            
            if turn.was_cancelled:
                logger.info(f"Turn {turn.utterance_id}: cancelled during STT")
                return
            
            transcript = stt_result.get("text", "").strip()
            if not transcript:
                self._release_room_turn()
                turn.error = "No speech detected"
                turn.transition_to(TurnState.COMPLETE)
                await self._send_error(ErrorCode.STT_FAILED, "No speech detected")
                await self._send_turn_end("error")
                return
            
            turn.transcript = transcript
            turn.stt_provider = stt_result.get("provider", "unknown")
            logger.info(f"Turn {turn.utterance_id}: transcript = '{transcript}'")
            
            # Step 2: Forward Translation
            logger.info(f"Turn {turn.utterance_id}: starting translation")
            t0 = time.perf_counter()
            translated = await self.llm_orchestrator.translate(
                text=transcript,
                target_language=turn.target_language,
                tone=turn.tone
            )
            forward_ms = (time.perf_counter() - t0) * 1000.0
            
            if turn.was_cancelled:
                logger.info(f"Turn {turn.utterance_id}: cancelled during translation")
                return
            
            turn.translated_text = translated
            logger.info(f"Turn {turn.utterance_id}: translated = '{translated}'")
            
            # Step 3: Back-translation for verification
            logger.info(f"Turn {turn.utterance_id}: starting back-translation")
            t0 = time.perf_counter()
            back_translated = await self.llm_orchestrator.back_translate(
                text=translated,
                source_language=turn.review_language
            )
            back_ms = (time.perf_counter() - t0) * 1000.0
            
            if turn.was_cancelled:
                logger.info(f"Turn {turn.utterance_id}: cancelled during back-translation")
                return
            
            turn.back_translation = back_translated
            logger.info(f"Turn {turn.utterance_id}: back-translated = '{back_translated}'")
            
            # Step 4: Present for review (speaker only approves; TTS never
            # runs before explicit approval).
            turn.transition_to(TurnState.REVIEW)
            await self._send_review(turn)
            to_review_ms = (time.perf_counter() - t_pipeline_start) * 1000.0
            logger.info(
                f"TIMING turn={turn.utterance_id} stage=to_review "
                f"stt_ms={stt_ms:.0f} forward_ms={forward_ms:.0f} "
                f"back_ms={back_ms:.0f} total_to_review_ms={to_review_ms:.0f}"
            )
            logger.info(f"Turn {turn.utterance_id}: presented for review")
            
        except asyncio.CancelledError:
            logger.info(f"Turn {turn.utterance_id}: processing cancelled")
        except Exception as e:
            logger.error(f"Turn {turn.utterance_id}: processing error: {e}", exc_info=True)
            self._release_room_turn()
            turn.error = str(e)
            turn.transition_to(TurnState.COMPLETE)
            await self._send_error(ErrorCode.TRANSLATION_FAILED, str(e))
            await self._send_turn_end("error")
    
    async def _handle_approve(self, request: ApproveRequest):
        """Handle approval of translation for TTS."""
        turn = self.turn_manager.current_turn
        
        if not turn:
            await self._send_error(ErrorCode.NO_ACTIVE_TURN, "No active turn")
            return
        
        if turn.utterance_id != request.utterance_id:
            await self._send_error(ErrorCode.INVALID_UTTERANCE, "Invalid utterance ID")
            return
        
        if turn.state != TurnState.REVIEW:
            await self._send_error(ErrorCode.NO_ACTIVE_TURN, "Turn is not in review state")
            return
        
        if turn.was_cancelled:
            logger.info(f"Turn {turn.utterance_id}: approval received after cancellation, ignoring")
            return
        
        # Synthesize speech
        await self._synthesize_and_send(turn)
    
    async def _synthesize_and_send(self, turn):
        """Synthesize TTS and send audio to client."""
        try:
            turn.transition_to(TurnState.SYNTHESIZING)

            # Synthesize
            t0 = time.perf_counter()
            wav_bytes = await self.tts_router.synthesize(
                text=turn.translated_text,
                language=turn.target_language
            )
            tts_ms = (time.perf_counter() - t0) * 1000.0
            logger.info(f"TIMING turn={turn.utterance_id} stage=tts tts_ms={tts_ms:.0f}")
            
            if turn.was_cancelled:
                logger.info(f"Turn {turn.utterance_id}: cancelled during TTS")
                self._release_room_turn()
                return

            # Room mode: route audio to the PEER, never back to the speaker.
            if self.room and self.participant:
                peer = self.room.get_peer(self.participant.participant_id)
                if peer is None:
                    logger.info(f"Turn {turn.utterance_id}: peer left before delivery")
                    self._release_room_turn()
                    turn.transition_to(TurnState.COMPLETE)
                    await self._send_turn_end("error")
                    return
                await peer.websocket.send_bytes(wav_bytes)
                logger.info(f"Turn {turn.utterance_id}: audio routed to peer {peer.participant_id}")
            else:
                await self.websocket.send_bytes(wav_bytes)

        except Exception as e:
            logger.error(f"Turn {turn.utterance_id}: TTS error: {e}")
            self._release_room_turn()
            turn.error = str(e)
            turn.transition_to(TurnState.COMPLETE)
            await self._send_error(ErrorCode.TTS_FAILED, str(e))
            await self._send_turn_end("error")
            return

        # Mark complete
        turn.transition_to(TurnState.COMPLETE)
        await self._send_turn_end("approved")

        # Room mode: hold the room turn until peer playback completes.
        # The peer must send playback_complete (or the timeout fires).
        if self.room and self.participant:
            self._start_playback_timeout()

        logger.info(f"Turn {turn.utterance_id}: audio sent, turn complete ({len(wav_bytes)} bytes, tts_ms={tts_ms:.0f})")
    
    async def _handle_cancel(self, request: CancelRequest):
        """Handle cancellation of current turn."""
        turn = self.turn_manager.current_turn
        
        if not turn:
            logger.info("Cancel received but no active turn")
            return
        
        if request.utterance_id and turn.utterance_id != request.utterance_id:
            await self._send_error(ErrorCode.INVALID_UTTERANCE, "Invalid utterance ID")
            return
        
        if not turn.is_active:
            logger.info(f"Turn {turn.utterance_id}: cancel received but turn not active")
            return
        
        # Cancel the turn
        self.turn_manager.cancel_current_turn()
        self._release_room_turn()
        
        # Cancel processing task
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
        
        logger.info(f"Turn {turn.utterance_id}: cancelled by user")
        await self._send_turn_end("cancelled")

    async def _handle_peer_left_message(self, data: dict):
        """The peer disconnected: end any pending turn safely.

        Unsafe pending work (a review awaiting approval, or inference whose
        result would be delivered to a recipient that no longer exists) must
        not continue. Cancel the turn, release the room lock, and tell the
        speaker the turn ended. The room itself stays alive so the peer may
        rejoin within the grace period.
        """
        peer_id = data.get("participant_id", "unknown")
        turn = self.turn_manager.current_turn
        if turn and turn.is_active:
            self.turn_manager.cancel_current_turn()
            if self._current_task and not self._current_task.done():
                self._current_task.cancel()
            self._release_room_turn()
            logger.info(
                f"Room peer {peer_id} left: cancelled pending turn "
                f"{turn.utterance_id}")
            await self._send_turn_end("peer_left")
        else:
            logger.info(f"Room peer {peer_id} left; no pending turn to cancel")
    
    async def _send_ready(self):
        """Send ready message to client."""
        await self._send_message(ReadyResponse(session_id=str(self.session_id)))
    
    async def _send_review(self, turn):
        """Send translation review to client."""
        await self._send_message(ReviewResponse(
            utterance_id=turn.utterance_id,
            transcript=turn.transcript,
            translated_text=turn.translated_text,
            back_translation=turn.back_translation,
            target_language=turn.target_language,
            verification_window_ms=settings.VERIFICATION_WINDOW_MS
        ))
    
    async def _send_turn_end(self, reason: str):
        """Send turn end message."""
        await self._send_message(TurnEndResponse(reason=reason))
    
    async def _send_error(self, code: ErrorCode, message: str):
        """Send error message."""
        await self._send_message(ErrorResponse(code=code, message=message))
    
    async def _send_message(self, message):
        """Send a Pydantic model as JSON."""
        try:
            await self.websocket.send_json(message.model_dump())
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
    
    # ---- Room management (Phase 7) ----

    async def _handle_create_room(self, request: CreateRoomRequest):
        """Create a room; this connection becomes the host."""
        if self.room is not None:
            await self._send_error(ErrorCode.INVALID_MESSAGE, "Already in a room")
            return
        participant = Participant(
            participant_id=f"p_{uuid.uuid4().hex[:8]}",
            token=secrets.token_urlsafe(24),
            websocket=self.websocket,
        )
        participant.listening_language = request.listening_language
        participant.review_language = request.review_language
        participant.tone = request.tone
        room = await session_manager.create_room(participant)
        self.room = room
        self.participant = participant
        await self._send_message(RoomCreatedResponse(
            room_id=room.room_id,
            join_secret=room.join_secret,
            participant_id=participant.participant_id,
            token=participant.token,
        ))

    async def _handle_join_room(self, request: JoinRoomRequest):
        """Join an existing room with room ID + join secret, or rejoin a
        disconnected session with participant credentials."""
        if self.room is not None:
            await self._send_error(ErrorCode.INVALID_MESSAGE, "Already in a room")
            return
        # Join/rejoin attempts are rate-limited per connection; never trust
        # a client-supplied participant ID in FIRST-join mode (IDs are
        # server-assigned). In REJOIN mode the participant ID+token pair is
        # the credential and is verified server-side with compare_digest.
        client_key = str(self.session_id)
        prefs = {
            "listening_language": request.listening_language,
            "review_language": request.review_language,
            "tone": request.tone,
        }
        try:
            if request.participant_token:
                # ---- Rejoin mode (bounded grace period) ----
                room, participant = await session_manager.rejoin(
                    room_id=request.room_id,
                    participant_id=request.participant_id,
                    participant_token=request.participant_token,
                    websocket=self.websocket,
                    client_key=client_key,
                    prefs=prefs,
                )
                rejoined = True
                # Approvals/audio from before the disconnect are never
                # replayed: the pending turn was cancelled at disconnect
                # time and no old approval state is stored.
            else:
                room, participant = await session_manager.join_room(
                    room_id=request.room_id,
                    join_secret=request.join_secret,
                    websocket=self.websocket,
                    client_key=client_key,
                )
                rejoined = False
                participant.listening_language = request.listening_language
                participant.review_language = request.review_language
                participant.tone = request.tone
        except RoomError as e:
            code = {
                "rate_limited": ErrorCode.RATE_LIMITED,
                "not_found": ErrorCode.ROOM_NOT_FOUND,
                "invalid_secret": ErrorCode.INVALID_SECRET,
                "invalid_credentials": ErrorCode.INVALID_CREDENTIALS,
                "already_connected": ErrorCode.INVALID_CREDENTIALS,
                "room_full": ErrorCode.ROOM_FULL,
            }.get(e.code, ErrorCode.ROOM_NOT_FOUND)
            await self._send_error(code, e.message)
            return
        self.room = room
        self.participant = participant
        await self._send_message(JoinedResponse(
            room_id=room.room_id,
            participant_id=participant.participant_id,
            token=participant.token,
            listening_language=participant.listening_language,
            review_language=participant.review_language,
            tone=participant.tone,
            rejoined=rejoined,
        ))
        # Notify the peer
        peer = room.get_peer(participant.participant_id)
        if peer and peer.websocket is not None and peer.websocket is not self.websocket:
            try:
                await peer.websocket.send_json(PeerJoinedResponse(
                    room_id=room.room_id,
                    participant_id=participant.participant_id,
                    rejoined=rejoined).model_dump())
            except Exception:
                logger.warning("Failed to notify peer of join")

    async def _handle_update_prefs(self, request: UpdatePrefsRequest):
        """Update this participant's language/tone preferences."""
        if not self.participant:
            await self._send_error(ErrorCode.NOT_AUTHENTICATED, "Not in a room")
            return
        if request.listening_language:
            self.participant.listening_language = request.listening_language
        if request.review_language:
            self.participant.review_language = request.review_language
        if request.tone:
            self.participant.tone = request.tone
        await self._send_message(PrefsUpdatedResponse(
            listening_language=self.participant.listening_language,
            review_language=self.participant.review_language,
            tone=self.participant.tone,
        ))

    async def _handle_playback_complete(self):
        """Peer finished playing audio; release the room turn."""
        if self.room and self.participant:
            self._cancel_playback_timeout()
            # The ack comes from the LISTENER, but the turn is held by the
            # active speaker — release it on the speaker's behalf.
            self.room.release_turn_on_behalf(self.participant.participant_id)
            logger.info(f"Room {self.room.room_id}: playback complete, turn released")

    def _release_room_turn(self):
        """Release the room turn if this connection holds it."""
        if self.room and self.participant:
            self._cancel_playback_timeout()
            self.room.release_turn(self.participant.participant_id)

    def _start_playback_timeout(self):
        """Start the playback timeout watchdog that releases the room turn.

        The watchdog captures the room's turn generation; if the peer acks
        and the same speaker starts a new turn before the timeout fires,
        the generation has changed and this stale watchdog becomes a no-op
        instead of wrongly releasing the NEW turn's lock.
        """
        self._cancel_playback_timeout()
        generation = self.room.turn_generation

        async def _timeout():
            try:
                await asyncio.sleep(settings.PLAYBACK_TIMEOUT_SECONDS)
                if (self.room and self.participant
                        and self.room.turn_generation == generation
                        and self.room.active_speaker_id == self.participant.participant_id):
                    logger.warning(
                        f"Room {self.room.room_id}: playback timeout, releasing turn")
                    self.room.release_turn(self.participant.participant_id)
            except asyncio.CancelledError:
                pass

        self._playback_timeout_task = asyncio.create_task(_timeout())

    def _cancel_playback_timeout(self):
        if self._playback_timeout_task and not self._playback_timeout_task.done():
            self._playback_timeout_task.cancel()
        self._playback_timeout_task = None

    async def _cleanup(self):
        """Clean up resources on disconnect."""
        logger.info(f"Cleaning up session {self.session_id}")
        
        # Cancel current turn
        self.turn_manager.cancel_current_turn()
        
        # Cancel processing task
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            try:
                await self._current_task
            except asyncio.CancelledError:
                pass

        # Cancel playback timeout watchdog
        self._cancel_playback_timeout()

        # Room mode: cancel pending peer work and notify the peer. The
        # departing participant's identity/preferences stay in the room for
        # a bounded grace period so they can rejoin with their credentials.
        if self.room and self.participant:
            peer = self.room.get_peer(self.participant.participant_id)
            room_id = self.room.room_id
            pid = self.participant.participant_id
            self.room = None
            self.participant = None
            still_alive = session_manager.mark_disconnected(room_id, pid)
            if not still_alive:
                # Nobody connected remains: no rejoin is possible.
                session_manager.remove_room(room_id)
            if peer:
                try:
                    await peer.websocket.send_json(PeerLeftResponse(
                        room_id=room_id, participant_id=pid).model_dump())
                except Exception:
                    logger.warning("Failed to notify peer of leave")
            logger.info(f"Participant {pid} left room {room_id}")