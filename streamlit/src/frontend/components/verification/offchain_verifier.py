"""
Off-chain verification module.

Computes trust scores based on user-provided information including document uploads,
email quality, phone number format, real name validation, and social media links.
Provides a 0-100 off-chain trust score based on these factors.
"""

import re
from typing import List, Optional, Dict, Any


class OffChainVerifier:
    """
    Computes a 0-100 off-chain trust score based on 5 factors:
    1. Document Upload Check (0-20 pts)
    2. Email Quality Check (0-40 pts)
    3. Phone Number Format (0-20 pts)
    4. Real Name Check (0-10 pts)
    5. Optional Social Link Check (0-10 pts)
    """
    
    # Allowed MIME types for documents
    ALLOWED_MIME_TYPES = {
        "application/pdf",
        "image/png",
        "image/jpeg"
    }
    
    # Minimum file size in bytes (20 KB)
    MIN_FILE_SIZE = 20 * 1024  # 20 KB
    
    # Trusted email domains
    TRUSTED_DOMAINS = {
        "gmail.com",
        "outlook.com",
        "icloud.com",
        "proton.me"
    }
    
    # Common disposable email domains (sample list)
    DISPOSABLE_DOMAINS = {
        "tempmail.com",
        "10minutemail.com",
        "guerrillamail.com",
        "mailinator.com",
        "throwaway.email",
        "temp-mail.org",
        "yopmail.com"
    }
    
    def __init__(self):
        pass
    
    def verify_document_upload(self, uploaded_files: Optional[List[Any]]) -> int:
        """
        Document Upload Check (0-20 pts)
        - No files uploaded → 0
        - 1-2 valid files → up to 20
        - Valid files must be: PDF, PNG, JPEG and > 20 KB
        """
        if not uploaded_files or len(uploaded_files) == 0:
            return 0
        
        valid_files = []
        for file in uploaded_files:
            # Check MIME type
            if hasattr(file, 'type') and file.type in self.ALLOWED_MIME_TYPES:
                # Check file size
                if hasattr(file, 'size') and file.size > self.MIN_FILE_SIZE:
                    valid_files.append(file)
                elif hasattr(file, 'read'):
                    # For Streamlit UploadedFile, read size
                    file.seek(0, 2)  # Seek to end
                    size = file.tell()
                    file.seek(0)  # Reset to beginning
                    if size > self.MIN_FILE_SIZE:
                        valid_files.append(file)
        
        if len(valid_files) == 0:
            return 0
        elif len(valid_files) == 1:
            return 10
        else:  # 2 or more valid files
            return 20
    
    def verify_email_quality(self, email: Optional[str]) -> int:
        """
        Email Quality Check (0-40 pts)
        - Must match basic email regex
        - Bonus if domain in trusted list (gmail.com, outlook.com, icloud.com, proton.me)
        - Disposable or invalid domain → low score
        """
        if not email or not isinstance(email, str):
            return 0
        
        email = email.strip().lower()
        
        # Basic email regex pattern
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        
        if not re.match(email_pattern, email):
            return 0
        
        # Extract domain
        try:
            domain = email.split('@')[1]
        except IndexError:
            return 0
        
        # Check if disposable domain
        if domain in self.DISPOSABLE_DOMAINS:
            return 5
        
        # Check if trusted domain
        if domain in self.TRUSTED_DOMAINS:
            return 40
        
        # Valid email with non-disposable domain
        return 20
    
    def verify_phone_number_format(self, phone: Optional[str]) -> int:
        """
        Phone Number Format (0-20 pts)
        - Check only format—not OTP
        - Accept + optional
        - 10-15 digits
        - Invalid format → low score
        """
        if not phone or not isinstance(phone, str):
            return 0
        
        phone = phone.strip()
        
        # Remove common separators (spaces, dashes, parentheses)
        cleaned = re.sub(r'[\s\-\(\)\.]', '', phone)
        
        # Check if starts with + (optional)
        if cleaned.startswith('+'):
            cleaned = cleaned[1:]
        
        # Check if all remaining characters are digits
        if not cleaned.isdigit():
            return 5
        
        # Check length (10-15 digits)
        if 10 <= len(cleaned) <= 15:
            return 20
        elif 8 <= len(cleaned) < 10 or 15 < len(cleaned) <= 17:
            return 10
        else:
            return 5
    
    def verify_real_name(self, name: Optional[str]) -> int:
        """
        Real Name Check (0-10 pts)
        - Non-empty, length > 2
        - Looks like a real name (letters + optional space)
        """
        if not name or not isinstance(name, str):
            return 0
        
        name = name.strip()
        
        # Check length
        if len(name) <= 2:
            return 0
        
        # Check if it looks like a real name (letters, spaces, hyphens, apostrophes)
        # Must contain at least one letter
        name_pattern = r'^[a-zA-Z\s\-\']+$'
        
        if not re.match(name_pattern, name):
            return 0
        
        # Check if it has at least one letter (not just spaces/special chars)
        if not re.search(r'[a-zA-Z]', name):
            return 0
        
        return 10
    
    def verify_social_link(self, social_link: Optional[str]) -> int:
        """
        Optional Social Link Check (0-10 pts)
        - GitHub or LinkedIn link → 10
        - Any other link → 5
        - Empty → 0
        """
        if not social_link or not isinstance(social_link, str):
            return 0
        
        social_link = social_link.strip()
        
        if len(social_link) == 0:
            return 0
        
        # Check if it's a valid URL format
        url_pattern = r'^https?://[^\s/$.?#].[^\s]*$'
        if not re.match(url_pattern, social_link):
            return 0
        
        social_link_lower = social_link.lower()
        
        # Check for GitHub or LinkedIn
        if 'github.com' in social_link_lower or 'linkedin.com' in social_link_lower:
            return 10
        
        # Any other valid link
        return 5
    
    def compute_offchain_score(
        self,
        uploaded_files: Optional[List[Any]] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        name: Optional[str] = None,
        social_link: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Compute the total off-chain trust score.
        
        Args:
            uploaded_files: List of uploaded file objects
            email: Email address string
            phone: Phone number string
            name: Full name string
            social_link: Social media profile URL string
        
        Returns:
            Dictionary with component scores and total_offchain_score
        """
        doc_score = self.verify_document_upload(uploaded_files)
        email_score = self.verify_email_quality(email)
        phone_score = self.verify_phone_number_format(phone)
        name_score = self.verify_real_name(name)
        social_score = self.verify_social_link(social_link)
        
        total_score = min(doc_score + email_score + phone_score + name_score + social_score, 100)
        
        return {
            "document_upload_score": doc_score,
            "email_quality_score": email_score,
            "phone_format_score": phone_score,
            "real_name_score": name_score,
            "social_link_score": social_score,
            "total_offchain_score": total_score
        }

