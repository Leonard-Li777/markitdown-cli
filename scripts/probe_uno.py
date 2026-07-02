"""Probe whether a LibreOffice UNO listener is responding on the given port."""
import sys, json
try:
    import uno
    from com.sun.star.connection import NoConnectException
    ctx = uno.getComponentContext()
    resolver = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", ctx)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 2083
    ctx2 = resolver.resolve(f"uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext")
    print("ok")
except Exception:
    print("fail")
    sys.exit(1)
