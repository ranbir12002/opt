# backend/utils/s3.py
import os
import logging
import boto3
from botocore.exceptions import ClientError
from typing import List, Dict, Any

logger = logging.getLogger("s3_utils")

# Load settings from environment variables
AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_S3_PRESIGNED_URL_TTL = int(os.getenv("AWS_S3_PRESIGNED_URL_TTL", 3600))

_s3_client = None

def get_s3_client():
    """Lazy initialization of boto3 S3 client to prevent startup failures when AWS is not configured."""
    global _s3_client
    if _s3_client is None:
        # Standard boto3 client initialization (picks up credentials from IAM Role or Env)
        _s3_client = boto3.client('s3', region_name=AWS_REGION)
    return _s3_client

def upload_file_bytes(session_id: str, filename: str, file_bytes: bytes, content_type: str) -> Dict[str, Any]:
    """
    Upload file bytes to S3 under the prefix uploads/{session_id}/{filename}.
    Returns a dictionary with the bucket, key, and a pre-signed URL.
    """
    if not AWS_S3_BUCKET:
        raise ValueError("AWS_S3_BUCKET environment variable is not set.")
    
    key = f"uploads/{session_id}/{filename}"
    client = get_s3_client()
    
    logger.info(f"Uploading file {filename} to S3 bucket {AWS_S3_BUCKET} at {key}")
    try:
        client.put_object(
            Bucket=AWS_S3_BUCKET,
            Key=key,
            Body=file_bytes,
            ContentType=content_type
        )
        url = get_presigned_url(key)
        return {
            "s3_bucket": AWS_S3_BUCKET,
            "s3_key": key,
            "s3_url": url
        }
    except ClientError as e:
        logger.error(f"Failed to upload file to S3: {e}", exc_info=True)
        raise RuntimeError(f"Failed to upload file to S3: {str(e)}")

def get_presigned_url(key: str, expiration: int = None) -> str:
    """
    Generate a pre-signed URL to retrieve a file from S3.
    """
    if not AWS_S3_BUCKET:
        raise ValueError("AWS_S3_BUCKET environment variable is not set.")
    
    if expiration is None:
        expiration = AWS_S3_PRESIGNED_URL_TTL
        
    client = get_s3_client()
    try:
        url = client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': AWS_S3_BUCKET,
                'Key': key
            },
            ExpiresIn=expiration
        )
        return url
    except ClientError as e:
        logger.error(f"Failed to generate pre-signed URL: {e}", exc_info=True)
        raise RuntimeError(f"Failed to generate pre-signed URL: {str(e)}")

def list_session_files(session_id: str) -> List[Dict[str, Any]]:
    """
    List all uploaded files under the prefix uploads/{session_id}/.
    Generates a pre-signed URL for each file.
    """
    if not AWS_S3_BUCKET:
        raise ValueError("AWS_S3_BUCKET environment variable is not set.")
        
    client = get_s3_client()
    prefix = f"uploads/{session_id}/"
    
    logger.info(f"Listing files for session_id: {session_id} under prefix {prefix}")
    try:
        response = client.list_objects_v2(
            Bucket=AWS_S3_BUCKET,
            Prefix=prefix
        )
        
        contents = response.get('Contents', [])
        files = []
        for item in contents:
            key = item['Key']
            # Get only the filename from the S3 key
            filename = os.path.basename(key)
            if not filename:  # Skip folder directories if any
                continue
                
            url = get_presigned_url(key)
            files.append({
                "filename": filename,
                "key": key,
                "size": item['Size'],
                "url": url,
                "last_modified": item['LastModified'].isoformat()
            })
            
        return files
    except ClientError as e:
        logger.error(f"Failed to list S3 files for session {session_id}: {e}", exc_info=True)
        raise RuntimeError(f"Failed to list S3 files: {str(e)}")
