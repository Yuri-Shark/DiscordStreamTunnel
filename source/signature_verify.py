"""Ed25519 verification through the OpenSSL already used by Python's TLS runtime."""
import ctypes
import os
from pathlib import Path
import ssl
import sys


def library():
    if os.name == 'nt':
        folders = [Path(getattr(sys,'_MEIPASS',Path(sys.executable).parent)), Path(sys.base_prefix)/'DLLs']
        candidates = [p for folder in folders for p in folder.glob('libcrypto*.dll')]
        expected = 'libcrypto-3' if ssl.OPENSSL_VERSION_INFO[0]>=3 else 'libcrypto-1_1'
        candidates.sort(key=lambda p:not p.name.lower().startswith(expected))
    else:
        from ctypes.util import find_library
        candidates = [find_library('crypto')]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            result = ctypes.CDLL(str(candidate))
            result.EVP_PKEY_new_raw_public_key
            return result
        except (OSError,AttributeError):
            continue
    raise ValueError('SignatureVerificationUnavailable')


def verify(public_key, signature, message):
    if len(public_key)!=32 or len(signature)!=64:
        raise ValueError('InvalidSignature')
    lib = library()
    pointer,size,integer = ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int
    definitions = {
        'OBJ_txt2nid':([ctypes.c_char_p],integer),
        'EVP_PKEY_new_raw_public_key':([integer,pointer,pointer,size],pointer),
        'EVP_MD_CTX_new':([],pointer),
        'EVP_DigestVerifyInit':([pointer,pointer,pointer,pointer,pointer],integer),
        'EVP_DigestVerify':([pointer,pointer,size,pointer,size],integer),
        'EVP_MD_CTX_free':([pointer],None),
        'EVP_PKEY_free':([pointer],None),
    }
    for name,(args,result) in definitions.items():
        function = getattr(lib,name)
        function.argtypes,function.restype = args,result
    key = lib.EVP_PKEY_new_raw_public_key(lib.OBJ_txt2nid(b'ED25519'),None,public_key,len(public_key))
    context = lib.EVP_MD_CTX_new()
    try:
        if not key or not context or lib.EVP_DigestVerifyInit(context,None,None,None,key)!=1:
            raise ValueError('SignatureVerificationUnavailable')
        if lib.EVP_DigestVerify(context,signature,len(signature),message,len(message))!=1:
            raise ValueError('InvalidSignature')
    finally:
        if context:lib.EVP_MD_CTX_free(context)
        if key:lib.EVP_PKEY_free(key)
