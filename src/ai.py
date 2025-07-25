import json
import requests
import os
import uuid
from typing import Optional, Dict, Any

from models import (
    TaskStatus, 
    AudioData,
    Transcript, 
    TranscriptGenerationData, 
    SegmentationData, 
    QuestionGenerationData,
    TranscriptParameters,
    SegmentationParameters,
    QuestionGenerationParameters,
)
from services.audio import AudioService
from services.transcription import TranscriptionService
from services.segmentation import SegmentationService
from services.question_generation import QuestionGenerationService
from services.storage import GCloudStorageService
from services.vector_store import VectorStoreService


# Get webhook configuration from environment
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL environment variable is required")
if not WEBHOOK_SECRET:
    raise ValueError("WEBHOOK_SECRET environment variable is required")

# Now we can safely use these as strings
webhook_url: str = WEBHOOK_URL
webhook_secret: str = WEBHOOK_SECRET



# Note: Removed the old JobState class and process_video_async function
# as they used in-memory job_states which is now replaced with MongoDB persistence

async def start_audio_extraction_task(job_id: str, url) -> Dict[str, Any]:
    print(f"start_audio_extraction_task called for job {job_id}")

    # Use webhook URL from environment
    current_webhook_url = webhook_url
    if not current_webhook_url.startswith("http://") and not current_webhook_url.startswith("https://"):
        current_webhook_url = "http://" + current_webhook_url

    print(f"Webhook URL: {current_webhook_url}")

    audio_service = AudioService()
    storage_service = GCloudStorageService()
    
    try:
        # Send webhook - Starting audio extraction
        audio_data = AudioData(status=TaskStatus.RUNNING)
        await send_webhook(current_webhook_url, job_id, webhook_secret, "AUDIO_EXTRACTION", audio_data)
        
        # Note: Job status updates are handled by the external system, not this read-only service
        
        # Extract audio from video
        print(f"Extracting audio from video: {url}")
        audio_file_path = await audio_service.extractAudio(url)
        print(f"Audio extracted successfully to: {audio_file_path}")
        
        # Upload audio file to Google Cloud Storage
        run_id = str(uuid.uuid4())[:8]  # Use first 8 characters of UUID for uniqueness
        file_name = f"audio/{job_id}_{run_id}_audio.wav"
        print(f"Uploading audio file to GCS with name: {file_name}")
        file_url = await storage_service.upload_file(audio_file_path, file_name, "audio/wav")
        os.remove(audio_file_path)  # Clean up local file after upload
        
        # Send webhook - Audio extraction completed
        audio_data = AudioData(
            status=TaskStatus.COMPLETED,
            fileName=file_name if file_url else None,
            fileUrl=file_url
        )
        
        # Note: Job status updates are handled by the external system, not this read-only service
        
        await send_webhook(current_webhook_url, job_id, webhook_secret, "AUDIO_EXTRACTION", audio_data)
        
        return {
            "status": "COMPLETED",
            "next_task": "TRANSCRIPT_GENERATION",
            "data": audio_data
        }
        
    except Exception as e:
        print(f"Error in audio extraction: {str(e)}")
        error_data = AudioData(status=TaskStatus.FAILED, error=str(e))
        await send_webhook(current_webhook_url, job_id, webhook_secret, "AUDIO_EXTRACTION", error_data)
        raise

async def start_transcript_generation_task(job_id: str, file: str, approval_data: TranscriptParameters) -> Dict[str, Any]:
    """Start transcript generation task - READ-ONLY database access"""
    print(f"start_transcript_generation_task called for job {job_id}")

    if not file:
        error_msg = f"No audio file found for job {job_id}"
        print(error_msg)
        raise ValueError(error_msg)
    
    # Use webhook URL from environment
    current_webhook_url = webhook_url
    if not current_webhook_url.startswith("http://") and not current_webhook_url.startswith("https://"):
        current_webhook_url = "http://" + current_webhook_url

    transcription_service = TranscriptionService()
    storage_service = GCloudStorageService()
    
    try:
        # Send webhook - Starting transcription
        transcript_data = TranscriptGenerationData(status=TaskStatus.RUNNING)
        await send_webhook(current_webhook_url, job_id, webhook_secret, "TRANSCRIPT_GENERATION", transcript_data)
        
        # Note: Job status updates are handled by the external system, not this read-only service
        
        # Generate transcript from audio
        print("Generating transcript from audio...")
        transcript = await transcription_service.transcribe(file, approval_data.modelSize, approval_data.language)
        
        # Note: Job status updates are handled by the external system, not this read-only service
        
        # Upload transcript to Google Cloud Storage
        run_id = str(uuid.uuid4())[:8]  # Use first 8 characters of UUID for uniqueness
        transcript_file_name = f"transcripts/{job_id}_{run_id}_transcript.json"
        transcript_file_url = await storage_service.upload_json_content(transcript, transcript_file_name)
        print(f"Transcript uploaded successfully to: {transcript_file_url}")
        # Send webhook - Transcription completed
        transcript_data = TranscriptGenerationData(
            status=TaskStatus.COMPLETED,
            fileName=transcript_file_name if transcript_file_url else None,
            fileUrl=transcript_file_url,
            parameters= approval_data
        )
        
        # Note: Job status updates are handled by the external system, not this read-only service
        
        await send_webhook(current_webhook_url, job_id, webhook_secret, "TRANSCRIPT_GENERATION", transcript_data)
        
        return {
            "status": "COMPLETED",
            "next_task": "SEGMENTATION",
            "data": transcript_data
        }
        
    except Exception as e:
        print(f"Error in transcription: {str(e)}")
        # Note: Job status updates are handled by the external system, not this read-only service
        
        error_data = TranscriptGenerationData(status=TaskStatus.FAILED, error=str(e))
        # Note: Job status updates are handled by the external system, not this read-only service
        
        await send_webhook(current_webhook_url, job_id, webhook_secret, "TRANSCRIPT_GENERATION", error_data)
        raise

async def start_segmentation_task(job_id: str, file: str, approval_data: Optional[SegmentationParameters] = None) -> Dict[str, Any]:
    """Start segmentation task - READ-ONLY database access"""
    # Use webhook URL from environment
    current_webhook_url = webhook_url
    print("file:", file)
    
    try:
        print(f"start_segmentation_task called for job {job_id}")
        # Get transcript from the specified transcription run using usePrevious (default to 0 if not provided)
        transcript = None
        if file:
            # Download the transcript file from GCloud bucket
            print(f"Downloading transcript from: {file}")
            try:
                response = requests.get(file)
                response.raise_for_status()
                transcript = response.text
                print(f"Successfully downloaded transcript: {len(transcript)} characters")
                # Parse transcript string to Transcript type
            except Exception as e:
                error_msg = f"Failed to download transcript from {file}: {str(e)}"
                print(error_msg)
                raise ValueError(error_msg)
    
        if not transcript:
            error_msg = f"No transcript found for job {job_id}. Check usePrevious index and transcription task status."
            print(error_msg)
            raise ValueError(error_msg)
        
        if not current_webhook_url.startswith("http://") and not current_webhook_url.startswith("https://"):
            current_webhook_url = "http://" + current_webhook_url
    
        print(f"Segmentation parameters: {SegmentationParameters}")
        
        # Send webhook - Starting segmentation
        segmentation_data = SegmentationData(status=TaskStatus.RUNNING)
        transcript = Transcript.model_validate_json(transcript)
        await send_webhook(current_webhook_url, job_id, webhook_secret, "SEGMENTATION", segmentation_data)
        
        # Note: Job status updates are handled by the external system, not this read-only service
        
        # Segment the transcript
        print("Segmenting transcript...")
        segmentation_service = SegmentationService()
        segments = await segmentation_service.segment_transcript(transcript, approval_data)
        
        # Send webhook - Segmentation completed
        segmentation_data = SegmentationData(
            status=TaskStatus.COMPLETED,
            segmentationMap=segments.segments,
            parameters=approval_data
        )
        
        # Note: Job status updates are handled by the external system, not this read-only service
        
        await send_webhook(current_webhook_url, job_id, webhook_secret, "SEGMENTATION", segmentation_data)
        
        return {
            "status": "COMPLETED",
            "next_task": "QUESTION_GENERATION",
            "data": segmentation_data
        }
        
    except Exception as e:
        print(f"Error in segmentation: {str(e)}")
        # Note: Job status updates are handled by the external system, not this read-only service
        
        error_data = SegmentationData(status=TaskStatus.FAILED, error=str(e))
        # Note: Job status updates are handled by the external system, not this read-only service
        
        await send_webhook(current_webhook_url, job_id, webhook_secret, "SEGMENTATION", error_data)
        raise

async def start_question_generation_task(job_id: str, segmentMap, approval_data: Optional[QuestionGenerationParameters] = None) -> Dict[str, Any]:
    """Start question generation task - READ-ONLY database access"""
    print(f"start_question_generation_task called for job {job_id}")
    # Use webhook URL from environment
    current_webhook_url = webhook_url

    try:
        if not segmentMap:
            error_msg = f"No segments found for job {job_id}. Check usePrevious index and segmentation task status."
            print(error_msg)
            raise ValueError(error_msg)
        
        if not current_webhook_url.startswith("http://") and not current_webhook_url.startswith("https://"):
            current_webhook_url = "http://" + current_webhook_url
            
        storage_service = GCloudStorageService()
    
        # Send webhook - Starting question generation
        question_gen_data = QuestionGenerationData(status=TaskStatus.RUNNING)
        await send_webhook(current_webhook_url, job_id, webhook_secret, "QUESTION_GENERATION", question_gen_data)
        
        # Generate questions from segments
        print("Generating questions...")
        question_service = QuestionGenerationService()
        questions = await question_service.generate_questions(
            segments=segmentMap,
            question_params=approval_data,
        )
        questions = [json.loads(q) for q in questions]
        questions = [i for s in questions for i in s]
        # Upload questions to Google Cloud Storage
        run_id = str(uuid.uuid4())[:8]  # Use first 8 characters of UUID for uniqueness
        questions_file_name = f"questions/{job_id}_{run_id}_questions.json"
        questions_file_url = await storage_service.upload_json_content(questions, questions_file_name)
        
        # Send webhook - Question generation completed
        if questions and len(questions) > 0:
            question_gen_data = QuestionGenerationData(
                status=TaskStatus.COMPLETED,
                fileName=questions_file_name if questions_file_url else None,
                fileUrl=questions_file_url,
                segmentMapUsed=segmentMap,
                parameters=approval_data
            )
            
            # Note: Job status updates are handled by the external system, not this read-only service
            
            await send_webhook(current_webhook_url, job_id, webhook_secret, "QUESTION_GENERATION", question_gen_data)
            
            return {
                "status": "COMPLETED",
                "next_task": None,
                "data": question_gen_data
            }
        else:
            question_gen_data = QuestionGenerationData(
                status=TaskStatus.FAILED,
                error="No questions were generated"
            )
            # Note: Job status updates are handled by the external system, not this read-only service
            
            await send_webhook(current_webhook_url, job_id, webhook_secret, "QUESTION_GENERATION", question_gen_data)
            
            return {
                "status": "COMPLETED",
                "next_task": None,
                "data": question_gen_data
            }
        
    except Exception as e:
        print(f"Error in question generation: {str(e)}")
        # Note: Job status updates are handled by the external system, not this read-only service
        
        error_data = QuestionGenerationData(status=TaskStatus.FAILED, error=str(e))
        # Note: Job status updates are handled by the external system, not this read-only service
        
        await send_webhook(current_webhook_url, job_id, webhook_secret, "QUESTION_GENERATION", error_data)
        raise

async def start_pdf_chunking_task(job_id: str, file: str) -> Dict[str, Any]:
    """Start PDF chunking and embeddings storage task in MongoDB Atlas Vector Storage"""
    print(f"start_pdf_chunking_task called for job {job_id}")

    if not file:
        error_msg = f"No PDF file found for job {job_id}"
        print(error_msg)
        raise ValueError(error_msg)

    # Use webhook URL from environment
    current_webhook_url = webhook_url
    if not current_webhook_url.startswith("http://") and not current_webhook_url.startswith("https://"):
        current_webhook_url = "http://" + current_webhook_url

    vector_store_service = VectorStoreService()
    
    try:
        # Send webhook - Starting PDF chunking
        pdf_chunking_data = {
            "status": TaskStatus.RUNNING,
            "fileName": file
        }
        await send_webhook(current_webhook_url, job_id, webhook_secret, "PDF_CHUNKING", pdf_chunking_data)
        
        # Chunk the PDF file
        print("Chunking PDF file...")
        from services.pdf_chunking import PDFChunkingService 
        pdf_chunker = PDFChunkingService()
        chunks = await pdf_chunker.chunk_pdf(file)
        

        # Send webhook - PDF chunking completed
        pdf_chunking_data = {
            "status": TaskStatus.RUNNING,
            "data": chunks
        }

        await send_webhook(current_webhook_url, job_id, webhook_secret, "PDF_CHUNKING_DONE", pdf_chunking_data)

        res = await vector_store_service.upload_chunks_embeddings(file, chunks)

        pdf_chunking_data = {
            "status": TaskStatus.COMPLETED,
        }
        
        # Note: Job status updates are handled by the external system, not this read-only service
        await send_webhook(current_webhook_url, job_id, webhook_secret, "CHUNKS_EMBEDDINGS_PUSHED_TO_MONGODB_VECTOR_STORE", pdf_chunking_data)
        
        return {
            "status": "COMPLETED",
            "next_task": None,
        }
    except Exception as e:
        print("Error in embeddings generations: ", str(e))


async def send_webhook(webhook_url: str, job_id: str, webhook_secret: str, task: str, data):
    """Send webhook notification"""
    # Convert data to dict if it's a Pydantic model
    if hasattr(data, 'dict'):
        data_dict = data.dict()
    else:
        data_dict = data
    
    webhook_data = {
        "task": task,
        "status": data_dict.get("status", "UNKNOWN"),
        "jobId": job_id,
        "data": data_dict
    }
    
    headers = {
        "Content-Type": "application/json",
        "x-webhook-signature": webhook_secret
    }
    
    try:
        print(f"Sending webhook to {webhook_url} for task {task}")
        response = requests.post(webhook_url, json=webhook_data, headers=headers, timeout=10)
        print(f"Webhook response: {response.status_code}")
        response.raise_for_status()
    except Exception as e:
        print(f"Error sending webhook: {str(e)}")
        # Don't raise the error, just log it
