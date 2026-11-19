using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace TranslatorDesktop
{
    public sealed record HealthInfo(
        string Status, bool ModelLoaded, string Device, int Workers, int BeamSize);

    public sealed record TranslateRequest(List<List<string>> Text, string TargetLang);

    public sealed record TranslateResponse(
        string TargetLang,
        List<List<string>> Translation,
        List<List<string>> DetectedLang,
        List<List<double>> Confidence,
        List<List<bool>>? Translated);

    public sealed record DetectRequest(List<string> Text);

    /// <summary>Per text: language code ("und" = no letters), English name, confidence, and
    /// whether the model can translate from it.</summary>
    public sealed record DetectResponse(
        List<string> DetectedLang, List<string> Language, List<double> Confidence, List<bool> Translatable);

    /// <summary>Languages the model can translate between, and languages it can detect.</summary>
    public sealed record LanguagesInfo(
        Dictionary<string, string> Languages, Dictionary<string, string>? Detectable);

    public sealed class TranslatorApiException : Exception
    {
        public TranslatorApiException(string message) : base(message) { }
    }

    /// <summary>JSON names in snake_case (target_lang), as used by the Python API.
    /// (.NET 5 has no built-in snake_case policy.)</summary>
    internal sealed class SnakeCaseNamingPolicy : JsonNamingPolicy
    {
        public override string ConvertName(string name)
        {
            var sb = new StringBuilder(name.Length + 4);
            for (var i = 0; i < name.Length; i++)
            {
                var c = name[i];
                if (char.IsUpper(c))
                {
                    if (i > 0) sb.Append('_');
                    sb.Append(char.ToLowerInvariant(c));
                }
                else
                {
                    sb.Append(c);
                }
            }
            return sb.ToString();
        }
    }

    /// <summary>HTTP client for the translator REST API (TranslatorServer) (a separate process).</summary>
    public sealed class TranslatorApi : IDisposable
    {
        private static readonly JsonSerializerOptions Json = new()
        {
            PropertyNamingPolicy = new SnakeCaseNamingPolicy(),
            DictionaryKeyPolicy = null, // language codes stay as they are
        };

        private readonly HttpClient _http;

        public TranslatorApi(Uri baseAddress)
        {
            BaseAddress = baseAddress;
            _http = new HttpClient { BaseAddress = baseAddress, Timeout = TimeSpan.FromMinutes(10) };
        }

        public Uri BaseAddress { get; }

        /// <summary>Returns null when the service is not reachable.</summary>
        public async Task<HealthInfo?> GetHealthAsync(CancellationToken ct = default)
        {
            using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            cts.CancelAfter(TimeSpan.FromSeconds(3));
            try
            {
                return await _http.GetFromJsonAsync<HealthInfo>("/health", Json, cts.Token);
            }
            catch (Exception e) when (e is HttpRequestException || e is JsonException
                                      || (e is TaskCanceledException && !ct.IsCancellationRequested))
            {
                return null;
            }
        }

        /// <summary>Returns null when the service is not reachable.</summary>
        public async Task<LanguagesInfo?> GetLanguagesAsync(CancellationToken ct = default)
        {
            try
            {
                return await _http.GetFromJsonAsync<LanguagesInfo>("/languages", Json, ct);
            }
            catch (Exception e) when (e is HttpRequestException || e is JsonException
                                      || (e is TaskCanceledException && !ct.IsCancellationRequested))
            {
                return null;
            }
        }

        /// <summary>Translates all texts (sent as one inner array) into the target language.</summary>
        public async Task<TranslateResponse> TranslateAsync(
            IReadOnlyList<string> texts, string targetLang, CancellationToken ct = default)
        {
            var request = new TranslateRequest(new List<List<string>> { texts.ToList() }, targetLang);
            HttpResponseMessage response;
            try
            {
                response = await _http.PostAsJsonAsync("/translate", request, Json, ct);
            }
            catch (HttpRequestException e)
            {
                throw new TranslatorApiException(
                    $"Translation service not reachable at {BaseAddress}: {e.Message}");
            }

            using (response)
            {
                if (!response.IsSuccessStatusCode)
                {
                    var body = await response.Content.ReadAsStringAsync(ct);
                    throw new TranslatorApiException(
                        $"Translation failed ({(int)response.StatusCode}): {ExtractDetail(body)}");
                }
                return await response.Content.ReadFromJsonAsync<TranslateResponse>(Json, ct)
                    ?? throw new TranslatorApiException("Empty response from translation service");
            }
        }

        /// <summary>Detects the language of each text (no translation).</summary>
        public async Task<DetectResponse> DetectAsync(IReadOnlyList<string> texts, CancellationToken ct = default)
        {
            HttpResponseMessage response;
            try
            {
                response = await _http.PostAsJsonAsync("/detect", new DetectRequest(texts.ToList()), Json, ct);
            }
            catch (HttpRequestException e)
            {
                throw new TranslatorApiException(
                    $"Translation service not reachable at {BaseAddress}: {e.Message}");
            }

            using (response)
            {
                if (!response.IsSuccessStatusCode)
                {
                    var body = await response.Content.ReadAsStringAsync(ct);
                    throw new TranslatorApiException(
                        $"Language detection failed ({(int)response.StatusCode}): {ExtractDetail(body)}");
                }
                return await response.Content.ReadFromJsonAsync<DetectResponse>(Json, ct)
                    ?? throw new TranslatorApiException("Empty response from translation service");
            }
        }

        private static string ExtractDetail(string body)
        {
            try
            {
                using var doc = JsonDocument.Parse(body);
                if (doc.RootElement.TryGetProperty("detail", out var detail))
                    return (detail.ValueKind == JsonValueKind.String ? detail.GetString() : detail.ToString()) ?? body;
            }
            catch (JsonException) { }
            return body;
        }

        public void Dispose() => _http.Dispose();
    }
}
