import React, { useState, useEffect, useCallback } from "react";
import { useStore } from "../../store";

interface PendingSuggestion { id: string; summary: string; suggestion: string; files: string[]; functions: number; }

export function DocGhostPanel({ onClose }: { onClose: () => void }) {
    const colors = useStore((s) => s.themeColors);
    const [pending, setPending] = useState<PendingSuggestion[]>([]);
    const [scanning, setScanning] = useState(false);
    const [docResult, setDocResult] = useState("");
    const [error, setError] = useState("");
    const API = "http://127.0.0.1:9876";

    const fetchPending = useCallback(async () => {
        try { setPending(await (await fetch(API+"/doc-ghost/pending")).json()); } catch(e:any) { setError(e.message); }
    }, []);

    useEffect(() => { fetchPending(); }, [fetchPending]);

    const scan = async () => {
        setScanning(true); setError("");
        try {
            const r = await fetch(API+"/doc-ghost/scan", {method:"POST"});
            const d = await r.json();
            fetchPending();
            if (d.feature_detected) {
                setDocResult("Feature detected!\n"+d.summary+"\n\n"+d.suggestion);
            } else {
                setDocResult(d.changes+" files scanned. No new features detected.");
            }
        } catch(e:any) { setError(e.message); }
        setScanning(false);
    };

    const generate = async (kind: string) => {
        setScanning(true);
        try {
            const r = await fetch(API+"/doc-ghost/generate", {
                method:"POST", headers:{"Content-Type":"application/json"},
                body:JSON.stringify({kind, file:"backend/api/__init__.py"})
            });
            const d = await r.json();
            setDocResult(d.doc||"No content generated.");
        } catch(e:any) { setError(e.message); }
        setScanning(false);
    };

    const dismiss = async (id: string) => {
        await fetch(API+"/doc-ghost/dismiss/"+id, {method:"POST"});
        fetchPending();
    };

    return (
        <div className="aurora-overlay">
            <div className="aurora-panel" style={{backgroundColor:colors.surface,borderColor:colors.border,color:colors.text,width:700,maxWidth:"92vw",maxHeight:"82vh",display:"flex",flexDirection:"column"}}>
                <div className="aurora-panel-header" style={{borderColor:colors.border}}>
                    <span>Doc Ghost</span>
                    <button onClick={onClose} style={{color:colors.textSecondary,background:"none",border:"none",cursor:"pointer",fontSize:16}}>X</button>
                </div>
                <div style={{padding:12,display:"flex",flexDirection:"column",gap:10,flex:1,overflow:"auto"}}>
                    <div style={{fontSize:12,color:colors.textSecondary,lineHeight:1.5}}>
                        Doc Ghost watches your code changes. When a feature is complete, it proactively suggests generating documentation, changelogs, and API docs.
                    </div>
                    <div style={{display:"flex",gap:6}}>
                        <button onClick={scan} disabled={scanning} style={{background:colors.accent,color:"#fff",border:"none",borderRadius:6,padding:"6px 14px",fontSize:12,cursor:"pointer"}}>Scan Workspace</button>
                        <button onClick={()=>generate("api")} style={{background:"transparent",color:colors.accent,border:`1px solid ${colors.accent}`,borderRadius:6,padding:"6px 14px",fontSize:12,cursor:"pointer"}}>Gen API Doc</button>
                        <button onClick={()=>generate("changelog")} style={{background:"transparent",color:colors.success,border:`1px solid ${colors.success}`,borderRadius:6,padding:"6px 14px",fontSize:12,cursor:"pointer"}}>Gen Changelog</button>
                    </div>
                    {error && <div style={{color:colors.error,fontSize:12,padding:6,background:colors.bg,borderRadius:4}}>{error}</div>}

                    {docResult && (
                        <div style={{background:colors.bg,padding:10,borderRadius:8,border:`1px solid ${colors.border}`}}>
                            <pre style={{whiteSpace:"pre-wrap",fontSize:11,color:colors.text,margin:0}}>{docResult}</pre>
                        </div>
                    )}

                    {pending.length>0 && (
                        <div>
                            <div style={{fontWeight:600,fontSize:12,marginBottom:8,color:colors.accent}}>Pending Suggestions ({pending.length})</div>
                            {pending.map(s=>(
                                <div key={s.id} style={{padding:"8px 10px",margin:"4px 0",background:colors.bgSecondary,borderRadius:8,border:`1px solid ${colors.border}`,fontSize:12}}>
                                    <div style={{fontWeight:600,color:colors.accent}}>{s.summary}</div>
                                    <div style={{color:colors.textSecondary,fontSize:11,marginTop:2}}>{s.suggestion}</div>
                                    <div style={{display:"flex",gap:6,marginTop:6}}>
                                        {s.files.slice(0,4).map(f=>(<span key={f} style={{background:colors.bg,padding:"1px 6px",borderRadius:3,fontSize:10,fontFamily:"monospace"}}>{f}</span>))}
                                        <button onClick={()=>dismiss(s.id)} style={{marginLeft:"auto",background:"transparent",color:colors.textSecondary,border:"none",cursor:"pointer",fontSize:11}}>Dismiss</button>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
