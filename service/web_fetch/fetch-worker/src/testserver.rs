//! A tiny HTTP server for the fetch tests.
//!
//! Real sockets, real redirects, real oversized and stalled bodies - the parts
//! of this stage worth testing are the ones a mocked HTTP client would hide.

#![cfg(test)]

use std::io::{BufRead, BufReader, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::thread;
use std::time::Duration;

/// What the server does when a path is requested.
#[derive(Debug, Clone)]
pub enum Reply {
    /// Status, content type, body.
    Body(u16, &'static str, Vec<u8>),
    /// Status, content type, body, extra header line (e.g. content-encoding).
    Encoded(u16, &'static str, Vec<u8>, &'static str),
    /// Status and Location.
    Redirect(u16, String),
    /// Headers, then a long pause before any body bytes.
    Stalled(&'static str),
    /// A response with no content-type at all.
    Untyped(Vec<u8>),
}

pub struct TestServer {
    address: SocketAddr,
    requests: Arc<AtomicUsize>,
}

impl TestServer {
    /// Start a server whose routes are decided by `route`.
    pub fn start(route: impl Fn(&str) -> Option<Reply> + Send + Sync + 'static) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind fixture server");
        let address = listener.local_addr().expect("fixture server address");
        let requests = Arc::new(AtomicUsize::new(0));
        let counter = requests.clone();
        let route = Arc::new(route);
        thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(stream) = stream else { break };
                counter.fetch_add(1, Ordering::SeqCst);
                let route = route.clone();
                thread::spawn(move || serve(stream, route.as_ref()));
            }
        });
        Self { address, requests }
    }

    pub fn port(&self) -> u16 {
        self.address.port()
    }

    pub fn requests(&self) -> usize {
        self.requests.load(Ordering::SeqCst)
    }
}

fn serve(mut stream: TcpStream, route: &(impl Fn(&str) -> Option<Reply> + ?Sized)) {
    let mut reader = BufReader::new(stream.try_clone().expect("clone stream"));
    let mut request_line = String::new();
    if reader.read_line(&mut request_line).is_err() {
        return;
    }
    // Drain the headers so the client sees a well-formed exchange.
    loop {
        let mut line = String::new();
        match reader.read_line(&mut line) {
            Ok(0) => break,
            Ok(_) if line.trim().is_empty() => break,
            Ok(_) => {}
            Err(_) => return,
        }
    }
    let path = request_line
        .split_whitespace()
        .nth(1)
        .unwrap_or("/")
        .to_string();

    match route(&path) {
        Some(Reply::Body(status, content_type, body)) => {
            write_response(
                &mut stream,
                status,
                &[&format!("content-type: {content_type}")],
                &body,
            );
        }
        Some(Reply::Encoded(status, content_type, body, extra)) => {
            write_response(
                &mut stream,
                status,
                &[&format!("content-type: {content_type}"), extra],
                &body,
            );
        }
        Some(Reply::Redirect(status, location)) => {
            write_response(
                &mut stream,
                status,
                &[&format!("location: {location}"), "content-type: text/html"],
                b"",
            );
        }
        Some(Reply::Stalled(content_type)) => {
            let header = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: {content_type}\r\ncontent-length: 64\r\nconnection: close\r\n\r\n"
            );
            let _ = stream.write_all(header.as_bytes());
            let _ = stream.flush();
            thread::sleep(Duration::from_secs(5));
        }
        Some(Reply::Untyped(body)) => {
            write_response(&mut stream, 200, &[], &body);
        }
        None => write_response(
            &mut stream,
            404,
            &["content-type: text/plain"],
            b"not found",
        ),
    }
}

fn write_response(stream: &mut TcpStream, status: u16, headers: &[&str], body: &[u8]) {
    let mut response = format!(
        "HTTP/1.1 {status} X\r\ncontent-length: {}\r\nconnection: close\r\n",
        body.len()
    );
    for header in headers {
        response.push_str(header);
        response.push_str("\r\n");
    }
    response.push_str("\r\n");
    let _ = stream.write_all(response.as_bytes());
    let _ = stream.write_all(body);
    let _ = stream.flush();
}
