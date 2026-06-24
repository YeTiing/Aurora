import React, { useState, useEffect, useCallback } from "react";
import { useStore } from "../../store";
import { t } from "../../i18n";

interface RESession { id: string; url: string; scene: string; total: number; apis: number; js_files: number; hooks: number; db_size: number; }
interface RERequest { id: string; seq: number; method: string; path: string; url: string; response_status: number; content_type: string; is_js: boolean; is_streaming: boolean; }
interface REDetail { id: string; method: string; url: string; request_headers: any; request_body: string; response_status: number; response_headers: any; response_body: string; content_type: string; }
interface REAnalysis { session_id?: string; scenes?: Array<{scene:string;score:number}>; auth_tokens?: Array<{type:string;value:string}>; crypto?: Array<{algorithm:string;count:number}>; api_endpoints?: string[]; stats?: any; }
interface RETrace { sign_functions?: any[]; key_variables?: any[]; call_chain?: any[]; timestamp_nonce?: string[]; summary?: string; }

export function RePanel({ onClose }: { onClose: () => void }) {
    const colors = useStore((s) => s.themeColors);
    const [sessions, set所有抓包会话] = useState<RESession[]>([]);
    const [activeSession, setActiveSession] = useState("");
    const [requests, setRequests] = useState<RERequest[]>([]);
    const [analysis, setAnalysis] = useState<REAnalysis | null>(null);
    const [captureUrl, setCaptureUrl] = useState("");
    const [capturing, setCapturing] = useState(false);
    const [loading, setLoading] = useState(false);
    const [activeTab, setActiveTab] = useState<"sessions"|"requests"|"analysis"|"deobfuscate">("sessions");
    const [error, setError] = useState("");

    // Detail view
    const [detail, setDetail] = useState<REDetail | null>(null);
    const [curlCmd, setCurlCmd] = useState("");
    const [replayResult, setReplayResult] = useState<any>(null);

    // Deobfuscate tab
    const [deobCode, setDeobCode] = useState("");
    const [deobResult, setDeobResult] = useState<any>(null);
    const [sigResult, setSigResult] = useState<RETrace | null>(null);

    // HAR import
    const [harFile, setHarFile] = useState("");

    const API = "http://127.0.0.1:9876";

    const fetch所有抓包会话 = useCallback(async () => {
        try { const r = await fetch(API+"/re/sessions"); set所有抓包会话(await r.json()); } catch(e:any) { setError(e.message); }
    }, []);

    useEffect(() => { fetch所有抓包会话(); }, [fetch所有抓包会话]);
    useEffect(() => { if (capturing) { const i = setInterval(fetch所有抓包会话, 2000); return () => clearInterval(i); } }, [capturing, fetch所有抓包会话]);

    const startCapture = async () => {
        setLoading(true);
        try {
            const r = await fetch(API+"/re/capture/start", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({url:captureUrl}) });
            const d = await r.json(); setCapturing(true); setActiveSession(d.session_id); fetch所有抓包会话();
        } catch(e:any) { setError(e.message); }
        setLoading(false);
    };

    const loadRequests = async (sid: string) => { setActiveSession(sid); setLoading(true); try { const r = await fetch(API+"/re/sessions/"+sid+"/requests?api_only=true"); setRequests((await r.json()).requests||[]); setActiveTab("requests"); } catch(e:any) { setError(e.message); } setLoading(false); };

    const viewDetail = async (rid: string) => {
        try {
            const [dr, cr] = await Promise.all([
                fetch(API+"/re/sessions/"+activeSession+"/requests/"+rid),
                fetch(API+"/re/sessions/"+activeSession+"/requests/"+rid+"/curl"),
            ]);
            setDetail(await dr.json());
            setCurlCmd((await cr.json()).curl || "");
        } catch(e:any) { setError(e.message); }
    };

    const replayRequest = async (rid: string) => {
        try {
            const r = await fetch(API+"/re/sessions/"+activeSession+"/replay/"+rid, { method:"POST", headers:{"Content-Type":"application/json"}, body:"{}" });
            setReplayResult(await r.json());
        } catch(e:any) { setError(e.message); }
    };

    const runAnalysis = async (sid: string) => { setLoading(true); try { const r = await fetch(API+"/re/analyze", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({session_id:sid}) }); setAnalysis(await r.json()); setActiveTab("analysis"); } catch(e:any) { setError(e.message); } setLoading(false); };

    const runDeobfuscate = async () => {
        if (!deobCode.trim()) return;
        setLoading(true);
        try {
            const [dr, sr] = await Promise.all([
                fetch(API+"/re/deobfuscate", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({code:deobCode}) }),
                fetch(API+"/re/signature", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({code:deobCode}) }),
            ]);
            setDeobResult(await dr.json());
            setSigResult(await sr.json());
        } catch(e:any) { setError(e.message); }
        setLoading(false);
    };

    const importHar = async () => {
        if (!harFile.trim()) return;
        setLoading(true);
        try {
            let har: any;
            try { har = JSON.parse(harFile); } catch { setError("Invalid HAR JSON"); setLoading(false); return; }
            const r = await fetch(API+"/re/import/har", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({har}) });
            const d = await r.json();
            setActiveSession(d.session_id);
            setActiveTab("requests");
            fetch所有抓包会话();
            loadRequests(d.session_id);
        } catch(e:any) { setError(e.message); }
        setLoading(false);
    };

    const exportHar = async (sid: string) => {
        try {
            const r = await fetch(API+"/re/sessions/"+sid+"/export/har");
            const har = await r.json();
            const blob = new Blob([JSON.stringify(har, null, 2)], {type:"application/json"});
            const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = sid+".har"; a.click();
        } catch(e:any) { setError(e.message); }
    };

    const copyCurl = () => { if(curlCmd) navigator.clipboard.writeText(curlCmd); };
    const deleteSession = async (sid: string) => { await fetch(API+"/re/sessions/"+sid, {method:"DELETE"}); setDetail(null); fetch所有抓包会话(); };

    const statusColors: Record<number,string> = {2:"#3fb950",3:"#d29922",4:"#f85149",5:"#f85149"};
    const get运行状态Color = (s: number) => statusColors[Math.floor(s/100)]||colors.textSecondary;

    return (
        <div className="aurora-overlay">
            <div className="aurora-panel re-panel" style={{backgroundColor:colors.surface,borderColor:colors.border,color:colors.text,width:900,maxWidth:"96vw",maxHeight:"88vh",display:"flex",flexDirection:"column"}}>
                {/* Header */}
                <div className="aurora-panel-header" style={{borderColor:colors.border}}>
                    <span>逆向工作台</span>
                    <div style={{display:"flex",gap:8}}>
                        {!capturing ? (
                            <button onClick={startCapture} style={{background:colors.accent,color:"#fff",border:"none",borderRadius:6,padding:"4px 10px",fontSize:11,cursor:"pointer"}}>+ 开始抓包</button>
                        ) : (
                            <button onClick={async()=>{await fetch(API+"/re/capture/stop",{method:"POST"});setCapturing(false);fetch所有抓包会话();}} style={{background:colors.error,color:"#fff",border:"none",borderRadius:6,padding:"4px 10px",fontSize:11,cursor:"pointer"}}>Stop</button>
                        )}
                        <button onClick={onClose} style={{color:colors.textSecondary,background:"none",border:"none",cursor:"pointer",fontSize:16}}>X</button>
                    </div>
                </div>

                {/* Capture bar */}
                <div style={{padding:"8px 16px",display:"flex",gap:8,borderBottom:`1px solid ${colors.border}`}}>
                    <input value={captureUrl} onChange={e=>setCaptureUrl(e.target.value)} placeholder="https://target.com" style={{flex:1,background:colors.bg,color:colors.text,border:`1px solid ${colors.border}`,borderRadius:6,padding:"5px 10px",fontSize:12}} />
                    {capturing && <span style={{color:colors.success,fontSize:12,whiteSpace:"nowrap"}}>{t("capturing")}</span>}
                </div>

                {/* Tabs */}
                <div style={{display:"flex",borderBottom:`1px solid ${colors.border}`}}>
                    {["sessions","requests","analysis","deobfuscate"].map(tab=>(
                        <button key={tab} onClick={()=>{setActiveTab(tab as any);if(tab==="requests"&&activeSession)loadRequests(activeSession);setDetail(null);setReplayResult(null);}}
                            style={{padding:"8px 16px",fontSize:12,border:"none",background:activeTab===tab?colors.accent:"transparent",color:activeTab===tab?"#fff":colors.textSecondary,cursor:"pointer",borderBottom:activeTab===tab?`2px solid ${colors.accent}`:"2px solid transparent"}}>
                            {tab==="sessions"?"所有抓包会话":tab==="requests"?"APIs":tab==="analysis"?"Analysis":"Deobfuscate"}
                        </button>
                    ))}
                </div>

                {/* Content */}
                <div style={{flex:1,overflow:"auto",padding:12}}>
                    {error && <div style={{color:colors.error,marginBottom:8,fontSize:12,padding:6,background:colors.bg,borderRadius:4}}>{error}<button onClick={()=>setError("")} style={{marginLeft:8,background:"none",border:"none",color:colors.textSecondary,cursor:"pointer"}}>X</button></div>}

                    {/* 所有抓包会话 */}
                    {activeTab==="sessions" && (
                        <div>
                            <div style={{marginBottom:10}}>
                                <div style={{fontSize:11,color:colors.textSecondary,marginBottom:4}}>导入 HAR 数据</div>
                                <div style={{display:"flex",gap:6}}>
                                    <textarea value={harFile} onChange={e=>setHarFile(e.target.value)} placeholder='在此粘贴以导入 HAR 抓包数据 (JSON 格式)...' style={{flex:1,minHeight:50,background:colors.bg,color:colors.text,border:`1px solid ${colors.border}`,borderRadius:6,padding:6,fontSize:11,resize:"vertical",fontFamily:"monospace"}} />
                                    <button onClick={importHar} style={{background:colors.accent,color:"#fff",border:"none",borderRadius:6,padding:"4px 12px",fontSize:11,cursor:"pointer",whiteSpace:"nowrap"}}>立刻导入</button>
                                </div>
                            </div>
                            {sessions.length===0 ? (
                                <div style={{color:colors.textSecondary,fontSize:12,textAlign:"center",padding:20}}>暂无任何抓包记录。立刻开启顶部抓包或导入 HAR 格式包。</div>
                            ) : sessions.map(s=>(
                                <div key={s.id} style={{padding:"10px 12px",margin:"4px 0",borderRadius:8,background:s.id===activeSession?colors.accent+"18":colors.bgSecondary,border:`1px solid ${s.id===activeSession?colors.accent:colors.border}`}}>
                                    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                                        <div style={{fontWeight:600,fontSize:12,maxWidth:300,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{s.url||"手动抓包记录"}</div>
                                        <div style={{display:"flex",gap:4}}>
                                            <button onClick={()=>loadRequests(s.id)} style={{background:colors.accent,color:"#fff",border:"none",borderRadius:4,padding:"3px 8px",fontSize:11,cursor:"pointer"}}>提取接口</button>
                                            <button onClick={()=>runAnalysis(s.id)} style={{background:"transparent",color:colors.accent,border:`1px solid ${colors.accent}`,borderRadius:4,padding:"3px 8px",fontSize:11,cursor:"pointer"}}>分析</button>
                                            <button onClick={()=>exportHar(s.id)} style={{background:"transparent",color:colors.textSecondary,border:`1px solid ${colors.border}`,borderRadius:4,padding:"3px 8px",fontSize:11,cursor:"pointer"}}>导出</button>
                                            <button onClick={()=>deleteSession(s.id)} style={{background:"transparent",color:colors.error,border:"none",fontSize:13,cursor:"pointer"}}>X</button>
                                        </div>
                                    </div>
                                    <div style={{fontSize:10,color:colors.textSecondary,marginTop:4}}>{s.apis} APIs | {s.js_files} JS | {s.hooks} hooks | {s.scene} | {(s.db_size/1024).toFixed(0)} KB</div>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Requests */}
                    {activeTab==="requests" && (
                        <div>
                            {requests.length===0 ? (
                                <div style={{color:colors.textSecondary,fontSize:12,textAlign:"center",padding:20}}>No API requests. Select session above.</div>
                            ) : requests.map(req=>(
                                <div key={req.id}>
                                    <div onClick={()=>{viewDetail(req.id);setReplayResult(null);}} style={{display:"flex",alignItems:"center",gap:8,padding:"6px 8px",fontSize:11,borderBottom:`1px solid ${colors.border}`,cursor:"pointer",background:detail?.id===req.id?colors.accent+"12":"transparent"}}>
                                        <span style={{fontWeight:600,color:get运行状态Color(req.response_status),minWidth:36}}>{req.method}</span>
                                        <span style={{color:colors.textSecondary,minWidth:28,textAlign:"right"}}>{req.response_status}</span>
                                        <span style={{flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{req.path}</span>
                                        {req.is_js&&<span style={{color:"#F7DF1E",fontSize:10}}>JS</span>}
                                        {req.is_streaming&&<span style={{color:colors.accent,fontSize:10}}>SSE</span>}
                                        <span style={{color:colors.textSecondary,fontSize:10}}>{(req.url.length>80?req.url.slice(0,77)+"...":req.url)}</span>
                                    </div>
                                    {/* Request detail inline */}
                                    {detail?.id===req.id && (
                                        <div style={{margin:"4px 0 8px 16px",padding:10,background:colors.bg,borderRadius:8,border:`1px solid ${colors.border}`,fontSize:11}}>
                                            <div style={{display:"flex",gap:6,marginBottom:8}}>
                                                <button onClick={copyCurl} style={{background:colors.accent,color:"#fff",border:"none",borderRadius:4,padding:"3px 8px",fontSize:11,cursor:"pointer"}}>Copy cURL</button>
                                                <button onClick={()=>replayRequest(req.id)} style={{background:"transparent",color:colors.accent,border:`1px solid ${colors.accent}`,borderRadius:4,padding:"3px 8px",fontSize:11,cursor:"pointer"}}>{t("replay")}</button>
                                                <button onClick={()=>{setDetail(null);setReplayResult(null);}} style={{background:"transparent",color:colors.textSecondary,border:"none",fontSize:11,cursor:"pointer"}}>关闭</button>
                                            </div>
                                            {curlCmd && (
                                                <div style={{marginBottom:8}}>
                                                    <div style={{color:colors.textSecondary,fontSize:10,marginBottom:2}}>cURL</div>
                                                    <pre style={{background:colors.bg,color:colors.text,padding:"6px 8px",borderRadius:4,fontSize:11,overflowX:"auto",whiteSpace:"pre-wrap",wordBreak:"break-all",maxHeight:120,overflowY:"auto"}}>{curlCmd}</pre>
                                                </div>
                                            )}
                                            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8}}>
                                                <div>
                                                    <div style={{fontWeight:600,color:colors.accent,marginBottom:4}}>Request</div>
                                                    <pre style={{background:colors.bg,color:colors.text,padding:6,borderRadius:4,fontSize:11,maxHeight:200,overflow:"auto",whiteSpace:"pre-wrap"}}>
                                                        {typeof detail.request_headers==="object"?JSON.stringify(detail.request_headers,null,2):String(detail.request_headers)}
                                                        {detail.request_body ? "\n\n"+detail.request_body.slice(0,3000) : ""}
                                                    </pre>
                                                </div>
                                                <div>
                                                    <div style={{fontWeight:600,color:colors.success,marginBottom:4}}>Response ({detail.response_status})</div>
                                                    <pre style={{background:colors.bg,color:colors.text,padding:6,borderRadius:4,fontSize:11,maxHeight:200,overflow:"auto",whiteSpace:"pre-wrap"}}>
                                                        {typeof detail.response_headers==="object"?JSON.stringify(detail.response_headers,null,2):String(detail.response_headers)}
                                                        {detail.response_body ? "\n\n"+detail.response_body.slice(0,3000) : ""}
                                                    </pre>
                                                </div>
                                            </div>
                                            {replayResult && (
                                                <div style={{marginTop:8,padding:8,background:replayResult.replayed?colors.success+"12":colors.error+"12",borderRadius:4}}>
                                                    <div style={{fontWeight:600,fontSize:11}}>{replayResult.replayed?"Replay OK":"Replay Failed"}</div>
                                                    {replayResult.status && <span style={{fontSize:10}}>运行状态: {replayResult.status}</span>}
                                                    {replayResult.error && <span style={{fontSize:10,color:colors.error}}>{replayResult.error}</span>}
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Analysis */}
                    {activeTab==="analysis" && (
                        <div>
                            {!analysis ? (
                                <div style={{color:colors.textSecondary,fontSize:12,textAlign:"center",padding:20}}>Select a session and click "Analyze".</div>
                            ) : (
                                <div style={{fontSize:12}}>
                                    {analysis.scenes&&analysis.scenes.length>0 && (
                                        <div style={{marginBottom:14}}>
                                            <div style={{fontWeight:600,marginBottom:6,color:colors.accent}}>{t("sceneDetection")}</div>
                                            {analysis.scenes.map((s:any)=>(
                                                <div key={s.scene} style={{display:"flex",gap:8,padding:"3px 0"}}><span>{s.scene}</span><span style={{color:colors.textSecondary}}>{s.score}%</span></div>
                                            ))}
                                        </div>
                                    )}
                                    {analysis.crypto&&analysis.crypto.length>0 && (
                                        <div style={{marginBottom:14}}>
                                            <div style={{fontWeight:600,marginBottom:6,color:colors.warning}}>Crypto</div>
                                            {analysis.crypto.map((c:any)=>(
                                                <div key={c.algorithm} style={{display:"flex",gap:8,padding:"3px 0"}}><span>{c.algorithm}</span><span style={{color:colors.textSecondary}}>{c.count} hits</span></div>
                                            ))}
                                        </div>
                                    )}
                                    {analysis.auth_tokens&&analysis.auth_tokens.length>0 && (
                                        <div style={{marginBottom:14}}>
                                            <div style={{fontWeight:600,marginBottom:6,color:colors.success}}>{t("authTokens")}</div>
                                            {analysis.auth_tokens.map((t:any,i:number)=>(
                                                <div key={i} style={{padding:"3px 0"}}><span style={{color:colors.textSecondary}}>{t.type}:</span> <span style={{fontFamily:"monospace",wordBreak:"break-all"}}>{t.value}</span></div>
                                            ))}
                                        </div>
                                    )}
                                    {analysis.api_endpoints&&analysis.api_endpoints.length>0 && (
                                        <div>
                                            <div style={{fontWeight:600,marginBottom:6}}>API Endpoints</div>
                                            {analysis.api_endpoints.map((ep:string,i:number)=>(
                                                <div key={i} style={{fontFamily:"monospace",fontSize:11,padding:"2px 0",color:colors.textSecondary}}>{ep}</div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Deobfuscate */}
                    {activeTab==="deobfuscate" && (
                        <div style={{display:"flex",flexDirection:"column",gap:10}}>
                            <textarea value={deobCode} onChange={e=>setDeobCode(e.target.value)}
                                placeholder="Paste JS code here to deobfuscate & trace signatures..."
                                style={{minHeight:120,background:colors.bg,color:colors.text,border:`1px solid ${colors.border}`,borderRadius:8,padding:10,fontSize:12,resize:"vertical",fontFamily:"monospace"}} />
                            <button onClick={runDeobfuscate} disabled={!deobCode.trim()}
                                style={{background:colors.accent,color:"#fff",border:"none",borderRadius:6,padding:"8px 16px",fontSize:12,cursor:"pointer",alignSelf:"flex-start"}}>
                                {t("deobfuscate")}
                            </button>
                            {deobResult && (
                                <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:10}}>
                                    <div>
                                        <div style={{fontWeight:600,fontSize:12,marginBottom:4,color:colors.accent}}>Obfuscation</div>
                                        <pre style={{background:colors.bg,color:colors.text,padding:8,borderRadius:6,fontSize:11,maxHeight:300,overflow:"auto"}}>
                                            {JSON.stringify(deobResult.obfuscation,null,2)}
                                        </pre>
                                        <div style={{fontWeight:600,fontSize:12,marginTop:8,marginBottom:4,color:colors.warning}}>{t("cryptoCalls")}</div>
                                        <pre style={{background:colors.bg,color:colors.text,padding:8,borderRadius:6,fontSize:11,maxHeight:200,overflow:"auto"}}>
                                            {JSON.stringify(deobResult.crypto_calls?.slice(0,20),null,2)}
                                        </pre>
                                    </div>
                                    <div>
                                        {sigResult && (
                                            <>
                                                <div style={{fontWeight:600,fontSize:12,marginBottom:4,color:colors.success}}>{t("signatureTrace")}</div>
                                                <div style={{background:colors.bg,padding:8,borderRadius:6,marginBottom:8,fontSize:12,color:colors.accent}}>{sigResult.summary}</div>
                                                {sigResult.key_variables&&sigResult.key_variables.length>0 && (
                                                    <>
                                                        <div style={{fontWeight:600,fontSize:11,marginBottom:2,color:colors.error}}>{t("keysFound")}</div>
                                                        <pre style={{background:colors.bg,color:colors.text,padding:8,borderRadius:6,fontSize:11,maxHeight:150,overflow:"auto"}}>
                                                            {JSON.stringify(sigResult.key_variables,null,2)}
                                                        </pre>
                                                    </>
                                                )}
                                            </>
                                        )}
                                        <div style={{fontWeight:600,fontSize:12,marginTop:8,marginBottom:4,color:colors.textSecondary}}>API Endpoints</div>
                                        <pre style={{background:colors.bg,color:colors.text,padding:8,borderRadius:6,fontSize:11,maxHeight:200,overflow:"auto"}}>
                                            {JSON.stringify(deobResult.api_endpoints,null,2)}
                                        </pre>
                                        {deobResult.secrets&&deobResult.secrets.length>0 && (
                                            <>
                                                <div style={{fontWeight:600,fontSize:12,marginTop:8,marginBottom:4,color:colors.error}}>{t("secretsFound")}</div>
                                                <pre style={{background:colors.bg,color:colors.text,padding:8,borderRadius:6,fontSize:11,maxHeight:150,overflow:"auto"}}>
                                                    {JSON.stringify(deobResult.secrets,null,2)}
                                                </pre>
                                            </>
                                        )}
                                    </div>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
