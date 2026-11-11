using System.Diagnostics;
using System.IO;
using System.Text;

namespace TranslatorDesktop;

/// <summary>
/// Starts the Python TranslatorService (uvicorn) as a hidden child process and stops it
/// again when the app closes. If the service is already running, it is reused instead.
/// </summary>
public sealed class ServiceHost : IDisposable
{
    private const int MaxLogLines = 40;
    private readonly Queue<string> _log = new();
    private Process? _process;

    public bool StartedByUs => _process is not null;

    public bool HasExited => _process is { HasExited: true };

    public string RecentLog
    {
        get { lock (_log) return string.Join(Environment.NewLine, _log); }
    }

    /// <summary>
    /// Finds the TranslatorService folder: TRANSLATOR_SERVICE_DIR if set, otherwise a
    /// "TranslatorService" folder next to the app or in any parent folder.
    /// </summary>
    public static string? FindServiceDir()
    {
        var fromEnv = Environment.GetEnvironmentVariable("TRANSLATOR_SERVICE_DIR");
        if (!string.IsNullOrWhiteSpace(fromEnv) && File.Exists(Path.Combine(fromEnv, "app.py")))
            return fromEnv;

        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
        {
            var candidate = Path.Combine(dir.FullName, "TranslatorService");
            if (File.Exists(Path.Combine(candidate, "app.py")))
                return candidate;
        }
        return null;
    }

    public void Start(string serviceDir, int port)
    {
        var python = Path.Combine(serviceDir, ".venv", "Scripts", "python.exe");
        if (!File.Exists(python))
            throw new FileNotFoundException(
                $"Python environment not found at {python}. Set it up as described in " +
                "TranslatorService/README.md.");

        var psi = new ProcessStartInfo(python)
        {
            WorkingDirectory = serviceDir,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        foreach (var arg in new[] { "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
                                    "--port", port.ToString() })
            psi.ArgumentList.Add(arg);
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        psi.Environment["PYTHONUNBUFFERED"] = "1";

        _process = Process.Start(psi) ?? throw new InvalidOperationException("Could not start Python");
        _process.OutputDataReceived += (_, e) => AddLog(e.Data);
        _process.ErrorDataReceived += (_, e) => AddLog(e.Data);
        _process.BeginOutputReadLine();
        _process.BeginErrorReadLine();
    }

    private void AddLog(string? line)
    {
        if (line is null) return;
        lock (_log)
        {
            _log.Enqueue(line);
            while (_log.Count > MaxLogLines) _log.Dequeue();
        }
    }

    public void Dispose()
    {
        try
        {
            if (_process is { HasExited: false })
                _process.Kill(entireProcessTree: true);
        }
        catch (InvalidOperationException) { }
        catch (System.ComponentModel.Win32Exception) { }
        _process?.Dispose();
        _process = null;
    }
}
