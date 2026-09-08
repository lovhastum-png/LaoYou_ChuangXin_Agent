package cn.laoyou.app

import android.net.Uri
import java.io.ByteArrayOutputStream
import java.io.Closeable
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory

/**
 * A loopback-only HTTP/WebSocket bridge used by the embedded call page.
 *
 * Android treats http://127.0.0.1 as a potentially trustworthy origin, while
 * an HTTP page loaded from a computer's LAN address cannot call getUserMedia.
 * The bridge keeps the browser origin local and forwards bytes to the fixed
 * backend configured by the user. It has no public listener, token logging,
 * or arbitrary proxy target.
 */
data class CallProxyTarget(val scheme: String, val host: String, val port: Int)

class LocalCallProxy private constructor(private val target: CallProxyTarget) : Closeable {
    companion object {
        const val MAX_HEADER_BYTES = 64 * 1024
        const val MAX_BODY_BYTES = 8 * 1024 * 1024
        val HOP_BY_HOP_HEADERS = setOf(
            "connection",
            "keep-alive",
            "proxy-connection",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailer",
            "transfer-encoding",
            "upgrade"
        )

        fun fromBackendUrl(value: String): LocalCallProxy {
            val uri = Uri.parse(value.trim())
            val scheme = uri.scheme?.lowercase(Locale.US).orEmpty()
            require(scheme == "http" || scheme == "https") { "仅支持HTTP或HTTPS服务地址" }
            val host = uri.host?.takeIf { it.isNotBlank() } ?: error("服务地址缺少主机名")
            val port = uri.port.takeIf { it > 0 } ?: if (scheme == "https") 443 else 80
            require(port in 1..65535) { "服务地址端口无效" }
            return LocalCallProxy(CallProxyTarget(scheme, host, port))
        }
    }

    private val server = ServerSocket(0, 16, InetAddress.getByName("127.0.0.1"))
    private val executor: ExecutorService = Executors.newCachedThreadPool { runnable ->
        Thread(runnable, "laoyou-call-proxy").apply { isDaemon = true }
    }
    private val activeSockets = ConcurrentHashMap.newKeySet<Socket>()
    @Volatile
    private var closed = false
    private val acceptThread = Thread(::acceptLoop, "laoyou-call-proxy-accept").apply { isDaemon = true }

    val port: Int get() = server.localPort

    fun start(): LocalCallProxy {
        acceptThread.start()
        return this
    }

    fun localUrlFor(remoteUrl: String): String {
        val remote = Uri.parse(remoteUrl)
        val path = remote.encodedPath?.takeIf { it.isNotEmpty() } ?: "/"
        val query = remote.encodedQuery?.let { "?$it" }.orEmpty()
        return "http://127.0.0.1:$port$path$query"
    }

    private fun acceptLoop() {
        while (!closed) {
            val client = runCatching { server.accept() }.getOrNull() ?: break
            if (closed) {
                closeQuietly(client)
                break
            }
            activeSockets += client
            try {
                executor.execute { handleClient(client) }
            } catch (_: RejectedExecutionException) {
                // close() can race this handoff after the closed check above.
                // Do not let the accept thread crash the app in that window.
                activeSockets.remove(client)
                closeQuietly(client)
                break
            }
        }
    }

    private fun handleClient(client: Socket) {
        var backend: Socket? = null
        try {
            client.tcpNoDelay = true
            val input = client.getInputStream()
            val output = client.getOutputStream()
            val requestHeader = readHeader(input) ?: return
            val request = parseRequest(requestHeader)
            require(isAllowedPath(request.path)) { "path not allowed" }
            val contentLength = request.headers["content-length"]?.toLongOrNull() ?: 0L
            require(contentLength in 0L..MAX_BODY_BYTES.toLong()) { "request body too large" }
            val body = if (contentLength > 0) readExactly(input, contentLength.toInt()) else byteArrayOf()
            val websocket = request.headers["upgrade"]?.equals("websocket", ignoreCase = true) == true
            val backendSocket = openBackendSocket()
            backend = backendSocket
            writeBackendRequest(backendSocket.getOutputStream(), request, body, websocket)
            if (websocket) {
                relayWebSocket(client, backendSocket, output)
            } else {
                backendSocket.getInputStream().copyTo(output)
                output.flush()
            }
        } catch (_: Exception) {
            if (!closed) sendBadGateway(client)
        } finally {
            closeQuietly(backend)
            backend?.let { activeSockets.remove(it) }
            closeQuietly(client)
            activeSockets.remove(client)
        }
    }

    private fun openBackendSocket(): Socket {
        val socket: Socket = if (target.scheme == "https") {
            val factory = SSLSocketFactory.getDefault() as SSLSocketFactory
            factory.createSocket()
        } else {
            Socket()
        }
        activeSockets += socket
        try {
            check(!closed) { "通话连接已关闭" }
            socket.tcpNoDelay = true
            socket.connect(InetSocketAddress(target.host, target.port), 10_000)
            if (socket is SSLSocket) {
                socket.sslParameters = socket.sslParameters.apply { endpointIdentificationAlgorithm = "HTTPS" }
                socket.startHandshake()
            }
            return socket
        } catch (error: Exception) {
            activeSockets.remove(socket)
            closeQuietly(socket)
            throw error
        }
    }

    private fun writeBackendRequest(
        backendOutput: OutputStream,
        request: ParsedRequest,
        body: ByteArray,
        websocket: Boolean
    ) {
        val host = if ((target.scheme == "http" && target.port == 80) || (target.scheme == "https" && target.port == 443)) {
            target.host
        } else {
            "${target.host}:${target.port}"
        }
        val builder = StringBuilder()
            .append(request.method).append(' ').append(request.path).append(" HTTP/1.1\r\n")
        request.headerLines.forEach { (name, value) ->
            val lower = name.lowercase(Locale.US)
            if (lower !in HOP_BY_HOP_HEADERS && lower != "host" && lower != "content-length") {
                builder.append(name).append(": ").append(value).append("\r\n")
            }
        }
        builder.append("Host: ").append(host).append("\r\n")
        if (websocket) {
            builder.append("Connection: Upgrade\r\n")
            builder.append("Upgrade: websocket\r\n")
        } else {
            builder.append("Connection: close\r\n")
        }
        if (body.isNotEmpty()) builder.append("Content-Length: ").append(body.size).append("\r\n")
        builder.append("\r\n")
        backendOutput.write(builder.toString().toByteArray(Charsets.ISO_8859_1))
        if (body.isNotEmpty()) backendOutput.write(body)
        backendOutput.flush()
    }

    private fun relayWebSocket(client: Socket, backend: Socket, clientOutput: OutputStream) {
        val backendInput = backend.getInputStream()
        val responseHeader = readHeader(backendInput) ?: error("empty websocket response")
        clientOutput.write(responseHeader)
        clientOutput.flush()
        val clientToBackend = Thread({ copyUntilClosed(client, backend) }, "laoyou-call-ws-up").apply { isDaemon = true }
        val backendToClient = Thread({ copyUntilClosed(backend, client) }, "laoyou-call-ws-down").apply { isDaemon = true }
        clientToBackend.start()
        backendToClient.start()
        try {
            clientToBackend.join()
        } finally {
            closeQuietly(client)
            closeQuietly(backend)
            backendToClient.join(1_000)
        }
    }

    private fun copyUntilClosed(from: Socket, to: Socket) {
        try {
            from.getInputStream().copyTo(to.getOutputStream())
            to.getOutputStream().flush()
        } catch (_: Exception) {
            // Closing the peer socket is the normal way to stop the other
            // pump. Never let that expected SocketException crash the app.
        } finally {
            // EOF or an exception in either direction must wake the other
            // pump; otherwise a half-open WebSocket can outlive the call.
            closeQuietly(from)
            closeQuietly(to)
        }
    }

    private fun sendBadGateway(client: Socket) {
        runCatching {
            val output = client.getOutputStream()
            output.write(
                "HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/plain; charset=utf-8\r\n".toByteArray(Charsets.ISO_8859_1)
            )
            output.write("Content-Length: 24\r\nConnection: close\r\n\r\n通话服务连接失败".toByteArray(Charsets.UTF_8))
            output.flush()
        }
    }

    private data class ParsedRequest(
        val method: String,
        val path: String,
        val headerLines: List<Pair<String, String>>,
        val headers: Map<String, String>
    )

    private fun parseRequest(header: ByteArray): ParsedRequest {
        val lines = String(header, Charsets.ISO_8859_1).split("\r\n")
        val requestParts = lines.firstOrNull()?.split(' ', limit = 3) ?: error("invalid request")
        require(requestParts.size == 3) { "invalid request line" }
        val pairs = lines.drop(1).filter { it.isNotEmpty() }.mapNotNull { line ->
            val separator = line.indexOf(':')
            if (separator <= 0) null else line.substring(0, separator) to line.substring(separator + 1).trim()
        }
        val headers = pairs.associate { (name, value) -> name.lowercase(Locale.US) to value }
        return ParsedRequest(requestParts[0], requestParts[1], pairs, headers)
    }

    private fun readHeader(input: InputStream): ByteArray? {
        val output = ByteArrayOutputStream()
        var matched = 0
        while (output.size() < MAX_HEADER_BYTES) {
            val next = input.read()
            if (next < 0) return null
            output.write(next)
            matched = when {
                matched == 0 && next == '\r'.code -> 1
                matched == 1 && next == '\n'.code -> 2
                matched == 2 && next == '\r'.code -> 3
                matched == 3 && next == '\n'.code -> return output.toByteArray()
                next == '\r'.code -> 1
                else -> 0
            }
        }
        error("request header too large")
    }

    private fun readExactly(input: InputStream, length: Int): ByteArray {
        val bytes = ByteArray(length)
        var offset = 0
        while (offset < length) {
            val read = input.read(bytes, offset, length - offset)
            if (read < 0) error("unexpected end of request")
            offset += read
        }
        return bytes
    }

    override fun close() {
        if (closed) return
        closed = true
        closeQuietly(server)
        activeSockets.toList().forEach { closeQuietly(it) }
        executor.shutdownNow()
        activeSockets.clear()
    }

    private fun closeQuietly(closeable: Closeable?) {
        runCatching { closeable?.close() }
    }

    private fun closeQuietly(socket: Socket?) {
        runCatching { socket?.close() }
    }

    private fun isAllowedPath(path: String): Boolean {
        return path == "/" || path == "/favicon.svg" ||
            path.startsWith("/call/") || path.startsWith("/api/") ||
            path.startsWith("/assets/") || path.startsWith("/ws/calls/")
    }
}
